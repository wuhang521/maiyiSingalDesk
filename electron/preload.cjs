const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("signalDesk", {
  pollMessages: (config) => ipcRenderer.invoke("ntfy:poll", config),
  publishMessage: (config) => ipcRenderer.invoke("ntfy:publish", config),
  subscribe: (config) => ipcRenderer.invoke("ntfy:subscribe", config),
  unsubscribe: (subscriptionId) => ipcRenderer.invoke("ntfy:unsubscribe", subscriptionId),
  onMessage: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("ntfy:event", listener);
    return () => ipcRenderer.removeListener("ntfy:event", listener);
  },
  openExternal: (url) => ipcRenderer.invoke("shell:open-external", url),
  showNotification: (notification) => ipcRenderer.invoke("notification:show", notification),
  setLaunchAtLogin: (enabled) => ipcRenderer.invoke("app:set-launch-at-login", enabled),
  getAppInfo: () => ipcRenderer.invoke("app:info"),
  checkForUpdates: () => ipcRenderer.invoke("app:check-for-updates"),
  getUpdateState: () => ipcRenderer.invoke("app:get-update-state"),
  downloadUpdate: () => ipcRenderer.invoke("app:download-update"),
  installUpdate: () => ipcRenderer.invoke("app:install-update"),
  onUpdateState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on("app:update-state", listener);
    return () => ipcRenderer.removeListener("app:update-state", listener);
  },
  loadAuthSession: () => ipcRenderer.invoke("auth:load"),
  saveAuthSession: (session) => ipcRenderer.invoke("auth:save", session),
  clearAuthSession: () => ipcRenderer.invoke("auth:clear"),
  getVideoConnection: (request) => ipcRenderer.invoke("video:get-connection", request),
  downloadKnowledgeAsset: (asset) => ipcRenderer.invoke("knowledge:download", asset),
  knowledgeWatchStatus: () => ipcRenderer.invoke("knowledge:watch-status"),
  knowledgeWatchStart: () => ipcRenderer.invoke("knowledge:watch-start"),
  knowledgeWatchStop: () => ipcRenderer.invoke("knowledge:watch-stop"),
  toggleTaskWidget: () => ipcRenderer.invoke("widget:toggle"),
  hideTaskWidget: () => ipcRenderer.invoke("widget:hide"),
  setTaskWidgetLocked: (locked) => ipcRenderer.invoke("widget:set-locked", locked),
  getTaskWidgetState: () => ipcRenderer.invoke("widget:get-state"),
  setTaskWidgetCollapsed: (collapsed) => ipcRenderer.invoke("widget:set-collapsed", collapsed),
  openTaskWorkspace: () => ipcRenderer.invoke("widget:open-main"),
  onNavigate: (callback) => {
    const listener = (_event, view) => callback(view);
    ipcRenderer.on("app:navigate", listener);
    return () => ipcRenderer.removeListener("app:navigate", listener);
  },
});
