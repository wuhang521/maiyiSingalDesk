const { app, BrowserWindow, dialog, ipcMain, net, shell, Notification, screen, safeStorage } = require("electron");
const fs = require("fs");
const path = require("path");

const subscriptions = new Map();
let mainWindow = null;
let taskWidgetWindow = null;
const knowledgeWatch = require("./knowledge-watch.cjs")({ app, ipcMain, dialog, getWindow: () => mainWindow, readSession: readAuthSession });
const desktopVideo = require("./video-desktop.cjs")({ app, net, readSession: readAuthSession });
const { autoUpdater } = require("electron-updater");
const desktopUpdater = require("./desktop-updater.cjs").createDesktopUpdater({
  app, dialog, autoUpdater,
  sendState: (state) => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("app:update-state", state);
  },
  canInstall: async () => {
    if (!mainWindow || mainWindow.isDestroyed()) return { allowed: false, message: "请从主工作台确认安装更新。" };
    const video = await desktopVideo.canRestart();
    if (!video.allowed) return video;
    if (!mainWindow || mainWindow.isDestroyed()) return { allowed: false, message: "请从主工作台确认安装更新。" };
    const clean = await mainWindow.webContents.executeJavaScript(`(() => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return !event.defaultPrevented;
    })()`);
    if (!clean) return { allowed: false, message: "当前内容尚未保存，请保存后再安装更新。" };
    return { allowed: true };
  },
});

app.setName("迈医协同");

if (process.env.SIGNALDESK_E2E_USER_DATA) {
  app.setPath("userData", path.resolve(process.env.SIGNALDESK_E2E_USER_DATA));
}

function lockWindowZoom(browserWindow) {
  const { webContents } = browserWindow;
  const resetZoom = () => {
    if (!webContents.isDestroyed()) webContents.setZoomFactor(1);
  };

  webContents.on("did-finish-load", resetZoom);
  webContents.on("zoom-changed", (event) => {
    event.preventDefault();
    resetZoom();
  });
  webContents.on("before-input-event", (event, input) => {
    const usesZoomModifier = input.control || input.meta;
    if (usesZoomModifier && ["+", "=", "-", "_", "0"].includes(input.key)) {
      event.preventDefault();
      resetZoom();
    }
  });
  resetZoom();
}

function createWindow() {
  const titleBarOptions =
    process.platform === "darwin"
      ? {
          titleBarStyle: "hiddenInset",
          trafficLightPosition: { x: 16, y: 14 },
        }
      : {
          titleBarStyle: "hidden",
          titleBarOverlay: {
            color: "#f6f7f5",
            symbolColor: "#202321",
            height: 44,
          },
        };

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1080,
    minHeight: 700,
    backgroundColor: "#f6f7f5",
    show: false,
    title: "迈医协同",
    ...titleBarOptions,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      zoomFactor: 1,
    },
  });

  lockWindowZoom(mainWindow);

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  mainWindow.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  mainWindow.webContents.on("will-attach-webview", (event) => event.preventDefault());
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url === mainWindow.webContents.getURL()) return;
    event.preventDefault();
    if (isSafeExternalUrl(url)) shell.openExternal(url);
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalUrl(url)) shell.openExternal(url);
    return { action: "deny" };
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

function widgetStatePath() {
  return path.join(app.getPath("userData"), "task-widget.json");
}

function authSessionPath() {
  return path.join(app.getPath("userData"), "auth-session.json");
}

function readAuthSession() {
  try {
    const stored = JSON.parse(fs.readFileSync(authSessionPath(), "utf8"));
    return JSON.parse(safeStorage.decryptString(Buffer.from(stored.data, "base64")));
  } catch {
    return null;
  }
}

function writeAuthSession(session) {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("当前系统暂时无法安全保存登录状态");
  }
  const data = safeStorage.encryptString(JSON.stringify(session)).toString("base64");
  fs.writeFileSync(authSessionPath(), JSON.stringify({ version: 1, data }), {
    encoding: "utf8",
    mode: 0o600,
  });
  return true;
}

function clearAuthSession() {
  try {
    fs.unlinkSync(authSessionPath());
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  return true;
}

function readWidgetState() {
  try {
    return JSON.parse(fs.readFileSync(widgetStatePath(), "utf8"));
  } catch {
    return { enabled: false };
  }
}

function writeWidgetState(update) {
  const next = { ...readWidgetState(), ...update };
  fs.writeFileSync(widgetStatePath(), JSON.stringify(next), "utf8");
  return next;
}

function createTaskWidgetWindow() {
  if (taskWidgetWindow && !taskWidgetWindow.isDestroyed()) {
    taskWidgetWindow.show();
    taskWidgetWindow.focus();
    return taskWidgetWindow;
  }

  const saved = readWidgetState();
  const width = Number.isFinite(saved.width) ? saved.width : 380;
  const expandedHeight = Number.isFinite(saved.height) ? saved.height : 378;
  const height = saved.collapsed ? 72 : expandedHeight;
  const workArea = screen.getPrimaryDisplay().workArea;
  const x = Number.isFinite(saved.x) ? saved.x : workArea.x + workArea.width - width - 24;
  const y = Number.isFinite(saved.y) ? saved.y : workArea.y + 24;

  taskWidgetWindow = new BrowserWindow({
    width,
    height,
    x,
    y,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    minWidth: 320,
    minHeight: saved.collapsed ? 72 : 260,
    maxWidth: 760,
    maxHeight: 900,
    resizable: !saved.locked && !saved.collapsed,
    movable: !saved.locked,
    skipTaskbar: true,
    show: false,
    title: "迈医协同 · 我的待办",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      zoomFactor: 1,
    },
  });

  lockWindowZoom(taskWidgetWindow);
  taskWidgetWindow.once("ready-to-show", () => taskWidgetWindow?.show());
  taskWidgetWindow.on("move", () => {
    if (!taskWidgetWindow || taskWidgetWindow.isDestroyed()) return;
    const bounds = taskWidgetWindow.getBounds();
    writeWidgetState({ x: bounds.x, y: bounds.y });
  });
  taskWidgetWindow.on("resize", () => {
    if (!taskWidgetWindow || taskWidgetWindow.isDestroyed()) return;
    const state = readWidgetState();
    if (state.collapsed) return;
    const bounds = taskWidgetWindow.getBounds();
    writeWidgetState({ width: bounds.width, height: bounds.height });
  });
  taskWidgetWindow.on("closed", () => {
    taskWidgetWindow = null;
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    const url = new URL(process.env.VITE_DEV_SERVER_URL);
    url.searchParams.set("widget", "1");
    taskWidgetWindow.loadURL(url.toString());
  } else {
    taskWidgetWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"), {
      query: { widget: "1" },
    });
  }
  return taskWidgetWindow;
}

function closeTaskWidget(disableStartup = true) {
  if (disableStartup) writeWidgetState({ enabled: false });
  if (taskWidgetWindow && !taskWidgetWindow.isDestroyed()) taskWidgetWindow.destroy();
  taskWidgetWindow = null;
}

function normalizeServer(server) {
  const value = String(server || "https://ntfy.sh").trim().replace(/\/+$/, "");
  const parsed = new URL(value);
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error("ntfy 服务器必须使用 HTTP 或 HTTPS");
  }
  return parsed.toString().replace(/\/$/, "");
}

function topicUrl(server, topic, suffix = "") {
  const normalizedTopic = String(topic || "").trim();
  if (!normalizedTopic || normalizedTopic.includes("/")) {
    throw new Error("Topic 不能为空，且不能包含斜杠");
  }
  return `${normalizeServer(server)}/${encodeURIComponent(normalizedTopic)}${suffix}`;
}

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${String(token).trim()}` } : {};
}

function encodeHeaderValue(value) {
  const text = String(value || "");
  return /^[\x20-\x7E]*$/.test(text)
    ? text
    : `=?UTF-8?B?${Buffer.from(text, "utf8").toString("base64")}?=`;
}

async function fetchWithRetry(url, options, maxAttempts = 3) {
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await net.fetch(url, options);
      if (response.ok || ![408, 429, 500, 502, 503, 504].includes(response.status)) {
        return response;
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    if (attempt < maxAttempts) await wait(300 * 2 ** (attempt - 1));
  }
  throw lastError || new Error("ntfy 请求失败");
}

function parseJsonLine(line) {
  try {
    return JSON.parse(line);
  } catch {
    return null;
  }
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function parseResponseError(response) {
  const body = await response.text().catch(() => "");
  try {
    const payload = JSON.parse(body);
    return payload.error || payload.message || `HTTP ${response.status}`;
  } catch {
    return body || `HTTP ${response.status}`;
  }
}

ipcMain.handle("ntfy:poll", async (_event, config) => {
  const since = encodeURIComponent(config.since || "12h");
  const url = topicUrl(config.server, config.topic, `/json?poll=1&since=${since}`);
  const response = await fetchWithRetry(url, { headers: authHeaders(config.token) });
  if (!response.ok) throw new Error(await parseResponseError(response));
  const text = await response.text();
  return text
    .split(/\r?\n/)
    .filter(Boolean)
    .map(parseJsonLine)
    .filter(Boolean)
    .filter((item) => item.event === "message");
});

ipcMain.handle("ntfy:publish", async (_event, config) => {
  const headers = {
    ...authHeaders(config.token),
    Title: encodeHeaderValue(config.title || "迈医协同测试消息"),
    Priority: String(config.priority || 3),
    Tags: Array.isArray(config.tags) ? config.tags.join(",") : "satellite",
    Markdown: "yes",
  };
  if (config.click) headers.Click = config.click;
  const response = await fetchWithRetry(topicUrl(config.server, config.topic), {
    method: "POST",
    headers,
    body: config.message || "频道连接成功。",
  });
  if (!response.ok) throw new Error(await parseResponseError(response));
  return response.json();
});

ipcMain.handle("ntfy:subscribe", async (_event, config) => {
  const subscriptionId = String(config.subscriptionId);
  stopSubscription(subscriptionId);
  const controller = new AbortController();
  subscriptions.set(subscriptionId, controller);
  streamSubscription(subscriptionId, config, controller, 0);
  return { subscriptionId };
});

ipcMain.handle("ntfy:unsubscribe", (_event, subscriptionId) => {
  stopSubscription(String(subscriptionId));
  return true;
});

ipcMain.handle("shell:open-external", (_event, url) => {
  if (!isSafeExternalUrl(url)) throw new Error("只允许打开 HTTP/HTTPS 链接");
  return shell.openExternal(url);
});

ipcMain.handle("notification:show", (_event, payload) => {
  if (!Notification.isSupported()) return false;
  const notification = new Notification({
    title: payload.title || "迈医协同",
    body: payload.body || "收到新消息",
    silent: Boolean(payload.silent),
  });
  notification.on("click", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
      mainWindow.webContents.send("ntfy:event", {
        type: "notification-click",
        messageId: payload.messageId,
      });
    }
  });
  notification.show();
  return true;
});

ipcMain.handle("app:set-launch-at-login", (_event, enabled) => {
  app.setLoginItemSettings({ openAtLogin: Boolean(enabled) });
  return app.getLoginItemSettings().openAtLogin;
});

ipcMain.handle("auth:load", () => readAuthSession());
ipcMain.handle("auth:save", (_event, session) => {
  desktopVideo.stop();
  knowledgeWatch.stop(true);
  writeAuthSession(session);
  taskWidgetWindow?.reload();
  return true;
});
ipcMain.handle("auth:clear", () => {
  desktopVideo.stop();
  knowledgeWatch.stop(true);
  clearAuthSession();
  taskWidgetWindow?.reload();
  return true;
});

ipcMain.handle("video:get-connection", async (event, request) => {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== event.sender.mainFrame) {
    throw new Error("只能从迈医协同主工作台启动视频制作");
  }
  return desktopVideo.connection(request);
});

ipcMain.handle("knowledge:download", async (event, asset) => {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== event.sender.mainFrame) {
    throw new Error("只能从主工作台保存知识库文件");
  }
  if (!asset || !/^[a-f0-9]{32}$/.test(asset.id) || typeof asset.filename !== "string" || asset.filename.length > 200) {
    throw new Error("知识库文件参数无效");
  }
  const session = readAuthSession();
  if (!session?.accessToken || !session?.backendUrl) throw new Error("请先登录后台");
  const filename = path.basename(asset.filename.replaceAll("\\", "/")).replace(/[<>:"|?*\x00-\x1f]/g, "_") || "知识库文件";
  const choice = await dialog.showSaveDialog(mainWindow, {
    title: "保存知识库原始文件", defaultPath: path.join(app.getPath("downloads"), filename),
    properties: ["showOverwriteConfirmation"],
  });
  if (choice.canceled || !choice.filePath) return false;
  const temporary = path.join(path.dirname(choice.filePath), `.signaldesk-${require("crypto").randomUUID()}.part`);
  let stream;
  let reader;
  try {
    const response = await net.fetch(`${session.backendUrl.replace(/\/+$/, "")}/api/v1/knowledge/assets/${asset.id}`, {
      headers: { Authorization: `Bearer ${session.accessToken}` }, signal: AbortSignal.timeout(300000), redirect: "error",
    });
    if (!response.ok || !response.body) throw new Error(`文件下载失败（${response.status}），请检查登录状态后重试`);
    stream = await fs.promises.open(temporary, "wx", 0o600);
    reader = response.body.getReader();
    let size = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 500 * 1024 * 1024) throw new Error("文件超过允许保存的大小");
      let offset = 0;
      while (offset < value.byteLength) {
        const { bytesWritten } = await stream.write(value, offset, value.byteLength - offset);
        if (!bytesWritten) throw new Error("文件写入失败");
        offset += bytesWritten;
      }
    }
    await stream.sync(); await stream.close(); stream = null;
    await fs.promises.rename(temporary, choice.filePath);
    return true;
  } finally {
    await reader?.cancel().catch(() => undefined);
    await stream?.close().catch(() => undefined);
    await fs.promises.unlink(temporary).catch((error) => { if (error.code !== "ENOENT") throw error; });
  }
});

ipcMain.handle("widget:toggle", () => {
  if (taskWidgetWindow && !taskWidgetWindow.isDestroyed()) {
    closeTaskWidget();
    return { visible: false, launchAtLogin: app.getLoginItemSettings().openAtLogin };
  }
  writeWidgetState({ enabled: true });
  if (!process.env.SIGNALDESK_E2E_USER_DATA) {
    app.setLoginItemSettings({ openAtLogin: true });
  }
  createTaskWidgetWindow();
  return { visible: true, launchAtLogin: true };
});

ipcMain.handle("widget:hide", () => {
  closeTaskWidget(false);
  return true;
});

ipcMain.handle("widget:set-locked", (_event, locked) => {
  if (!taskWidgetWindow || taskWidgetWindow.isDestroyed()) return false;
  const value = Boolean(locked);
  writeWidgetState({ locked: value });
  taskWidgetWindow.setMovable(!value);
  taskWidgetWindow.setResizable(!value && !readWidgetState().collapsed);
  return true;
});

ipcMain.handle("widget:get-state", () => {
  const state = readWidgetState();
  return { locked: Boolean(state.locked), collapsed: Boolean(state.collapsed) };
});

ipcMain.handle("widget:set-collapsed", (_event, collapsed) => {
  if (!taskWidgetWindow || taskWidgetWindow.isDestroyed()) return false;
  const value = Boolean(collapsed);
  const state = readWidgetState();
  if (value === Boolean(state.collapsed)) return value;
  if (value) {
    const bounds = taskWidgetWindow.getBounds();
    writeWidgetState({ collapsed: true, width: bounds.width, height: bounds.height });
    taskWidgetWindow.setResizable(false);
    taskWidgetWindow.setMinimumSize(320, 72);
    taskWidgetWindow.setBounds({ ...bounds, height: 72 }, true);
  } else {
    writeWidgetState({ collapsed: false });
    taskWidgetWindow.setMinimumSize(320, 260);
    taskWidgetWindow.setBounds({
      ...taskWidgetWindow.getBounds(),
      height: Number.isFinite(state.height) ? state.height : 378,
    }, true);
    taskWidgetWindow.setResizable(!state.locked);
  }
  return value;
});

ipcMain.handle("widget:open-main", () => {
  const needsLoad = !mainWindow || mainWindow.isDestroyed();
  if (needsLoad) createWindow();
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
  if (needsLoad) {
    mainWindow.webContents.once("did-finish-load", () => {
      mainWindow?.webContents.send("app:navigate", "tasks");
    });
  } else {
    mainWindow.webContents.send("app:navigate", "tasks");
  }
  return true;
});

ipcMain.handle("app:info", () => ({
  version: app.getVersion(),
  platform: process.platform,
  launchAtLogin: app.getLoginItemSettings().openAtLogin,
  backendUrl: process.env.SIGNALDESK_BACKEND_URL || "",
}));

function assertUpdateCaller(event) {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== event.sender.mainFrame) {
    throw new Error("只能从迈医协同主工作台操作软件更新");
  }
}
ipcMain.handle("app:get-update-state", (event) => { assertUpdateCaller(event); return desktopUpdater.getState(); });
ipcMain.handle("app:check-for-updates", (event) => { assertUpdateCaller(event); return desktopUpdater.check(); });
ipcMain.handle("app:download-update", (event) => { assertUpdateCaller(event); return desktopUpdater.download(); });
ipcMain.handle("app:install-update", async (event) => {
  assertUpdateCaller(event);
  const state = await desktopUpdater.install();
  return { started: state.status === "installing", reason: state.message };
});

async function streamSubscription(subscriptionId, config, controller, attempt) {
  try {
    const url = topicUrl(config.server, config.topic, "/json?since=10m");
    const response = await net.fetch(url, {
      headers: authHeaders(config.token),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(await parseResponseError(response));
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!controller.signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const payload = JSON.parse(line);
        if (payload.event === "message" && mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send("ntfy:event", {
            type: "message",
            subscriptionId,
            payload,
          });
        }
      }
    }
  } catch (error) {
    if (controller.signal.aborted) return;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("ntfy:event", {
        type: "status",
        subscriptionId,
        status: "reconnecting",
        error: error.message,
      });
    }
  }
  if (!controller.signal.aborted && subscriptions.get(subscriptionId) === controller) {
    const delay = Math.min(30000, 1000 * 2 ** Math.min(attempt, 5));
    setTimeout(() => streamSubscription(subscriptionId, config, controller, attempt + 1), delay);
  }
}

function stopSubscription(subscriptionId) {
  const controller = subscriptions.get(subscriptionId);
  if (controller) controller.abort();
  subscriptions.delete(subscriptionId);
}

function isSafeExternalUrl(value) {
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

const gotLock = app.requestSingleInstanceLock();
app.on("before-quit", () => { desktopUpdater.stop(); desktopVideo.stop(); });
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  app.whenReady().then(() => {
    app.setAppUserModelId("com.signaldesk.desktop");
    createWindow();
    desktopUpdater.start();
    if (readWidgetState().enabled) createTaskWidgetWindow();
  });

  app.on("window-all-closed", () => {
    for (const id of subscriptions.keys()) stopSubscription(id);
    if (process.platform !== "darwin") app.quit();
  });
}
