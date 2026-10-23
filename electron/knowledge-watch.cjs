// Local metadata observer only: no network, no file-content reads, no automatic startup.
const fs = require("fs");
const path = require("path");
module.exports = function install({ app, ipcMain, dialog, getWindow, readSession }) {
  let run = null; let state = { running: false, folder: "", message: "本地目录观察未开启", files: [] };
  function trusted(event) { const w = getWindow(); if (!w || event.sender !== w.webContents || event.senderFrame !== event.sender.mainFrame) throw new Error("只能在主客户端管理本地目录观察"); }
  function stop(reset = false) { if (run) clearInterval(run.timer); run = null; state = reset ? { running: false, folder: "", message: "本地目录观察未开启", files: [] } : { ...state, running: false, message: "已停止本地观察，没有自动上传任何文件" }; }
  function valid(job) { const s = readSession(); return run === job && s && s.accessToken === job.token && Date.parse(s.expiresAt) > Date.now(); }
  async function scan(job) {
    if (job.scanning) return;
    if (!valid(job)) { if (run === job) stop(true); return; }
    job.scanning = true;
    try {
      const root = await fs.promises.lstat(job.folder);
      if (!root.isDirectory() || root.isSymbolicLink() || await fs.promises.realpath(job.folder) !== job.folder) throw new Error("目录已变更，请停止后重新选择");
      const entries = await fs.promises.readdir(job.folder, { withFileTypes: true });
      if (entries.length > 1000) throw new Error("目录超过 1000 个条目，请改用专用小文件夹");
      const files = [];
      for (const entry of entries) {
        if (!entry.isFile() || entry.isSymbolicLink()) continue;
        const metadata = await fs.promises.lstat(path.join(job.folder, entry.name));
        if (metadata.isFile() && !metadata.isSymbolicLink()) files.push({ name: entry.name, size: metadata.size, updatedAt: metadata.mtimeMs });
      }
      if (valid(job)) state = { ...state, files: files.sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 100), message: "只在本机观察文件名称、大小和修改时间；不会自动读取内容或上传。请选择文件并确认上传。" };
    } catch { if (run === job) { stop(); state.message = "目录不可读、已变化或超过观察上限，请重新选择专用文件夹。未上传文件。"; } }
    finally { job.scanning = false; }
  }
  ipcMain.handle("knowledge:watch-status", event => { trusted(event); if (run && !valid(run)) stop(true); return state; });
  ipcMain.handle("knowledge:watch-stop", event => { trusted(event); stop(); return state; });
  ipcMain.handle("knowledge:watch-start", async event => {
    trusted(event); const session = readSession(); if (!session || Date.parse(session.expiresAt) <= Date.now()) throw new Error("请先登录本人账号");
    const selected = await dialog.showOpenDialog(getWindow(), { title: "选择专用文件夹（只观察本地元数据，不上传）", properties: ["openDirectory"] });
    if (selected.canceled || !selected.filePaths[0]) return state;
    if (readSession()?.accessToken !== session.accessToken) throw new Error("选择期间账号已改变，请重试");
    const chosen = path.resolve(selected.filePaths[0]); const metadata = await fs.promises.lstat(chosen);
    if (!metadata.isDirectory() || metadata.isSymbolicLink()) throw new Error("请选择实际目录，不支持目录链接");
    const folder = await fs.promises.realpath(chosen);
    if (folder === path.parse(folder).root || folder.toLowerCase() === app.getPath("home").toLowerCase()) throw new Error("请选择专用小文件夹，不支持磁盘根目录和用户主目录");
    stop(true); const job = { folder, token: session.accessToken, timer: null, scanning: false }; run = job;
    state = { running: true, folder, message: "正在本地观察…", files: [] }; await scan(job);
    if (valid(job)) job.timer = setInterval(() => void scan(job), 5000);
    return state;
  });
  return { stop };
};
