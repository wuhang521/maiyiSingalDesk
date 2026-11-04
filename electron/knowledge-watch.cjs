const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const supported = /\.(jpg|jpeg|png|txt|md|html?|pdf|srt|vtt|mp4|mov|mkv|webm|mp3|m4a|wav|aac|ogg|silk|slk|aud|amr)$/i;
const initial = () => ({ running: false, mode: "observe", folder: "", backend: "", message: "文件夹导入未开启", files: [], saved: 0, duplicates: 0, failed: 0 });
const identity = stat => [stat.dev, stat.ino, stat.size, stat.mtimeMs, stat.ctimeMs].join(":");
module.exports = function install({ app, ipcMain, dialog, getWindow, readSession, fetchImpl = globalThis.fetch, now = Date.now, settleMs = 5000 }) {
  let run = null, starting = false, state = initial();
  function trusted(event) {
    const window = getWindow();
    if (!window || event.sender !== window.webContents || event.senderFrame !== event.sender.mainFrame) throw new Error("只能从主工作台管理文件夹导入");
  }
  function valid(job) {
    const session = readSession();
    return run === job && session && session.accessToken === job.token && session.backendUrl === job.backend && Date.parse(session.expiresAt) > now();
  }
  function stop(reset = false) {
    if (run) { clearInterval(run.timer); run.controller?.abort(); }
    run = null;
    state = reset ? initial() : { ...state, running: false, message: "已停止。已保存资料保留；正在提交的文件可能已到达后台，请到知识库核对。" };
    return state;
  }
  async function sameDirectory(job) {
    const stat = await fs.promises.lstat(job.folder);
    if (!stat.isDirectory() || stat.isSymbolicLink() || await fs.promises.realpath(job.folder) !== job.folder) throw new Error("目录已变化");
  }
  async function upload(job, row, stamp) {
    await sameDirectory(job);
    const filename = path.join(job.folder, row.name);
    const stat = await fs.promises.lstat(filename);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink > 1 || identity(stat) !== stamp || await fs.promises.realpath(filename) !== filename) return;
    const handle = await fs.promises.open(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    let sent = false;
    try {
      if (identity(await handle.stat()) !== stamp) return;
      const hash = crypto.createHash("sha256"), buffer = Buffer.alloc(256 * 1024);
      let offset = 0;
      while (offset < row.size) {
        if (!valid(job)) return;
        const { bytesRead } = await handle.read(buffer, 0, Math.min(buffer.length, row.size - offset), offset);
        if (!bytesRead) throw new Error("文件尚未写入完成");
        hash.update(buffer.subarray(0, bytesRead)); offset += bytesRead;
      }
      if (!valid(job) || identity(await handle.stat()) !== stamp) return;
      const sha = hash.digest("hex");
      const key = crypto.createHash("sha256").update("folder-import-v1\n" + row.name + "\n" + sha).digest("hex");
      job.controller = new AbortController();
      const signal = AbortSignal.any([job.controller.signal, AbortSignal.timeout(300000)]);
      const body = (async function* () {
        let position = 0;
        while (position < row.size) {
          if (!valid(job) || signal.aborted) throw new Error("导入已停止");
          const part = Buffer.alloc(Math.min(256 * 1024, row.size - position));
          const { bytesRead } = await handle.read(part, 0, part.length, position);
          if (!bytesRead) throw new Error("文件读取中断");
          position += bytesRead; yield part.subarray(0, bytesRead);
        }
        if (identity(await handle.stat()) !== stamp) throw new Error("上传期间文件变化");
      })();
      sent = true;
      const response = await fetchImpl(job.backend.replace(/\/+$/, "") + "/api/v1/knowledge/capture/files", {
        method: "POST", redirect: "error", signal, duplex: "half", body,
        headers: { Authorization: "Bearer " + job.token, "Content-Type": "application/octet-stream", "Content-Length": String(row.size),
          "X-Filename-Base64": Buffer.from(row.name).toString("base64"), "X-Content-SHA256": sha, "Idempotency-Key": key },
      });
      const text = await response.text();
      if (text.length > 65536) throw new Error("后台响应超出限制，请核对资料状态");
      let result; try { result = JSON.parse(text); } catch { throw new Error("后台响应无效，请核对资料状态"); }
      if (!response.ok) {
        if ([401, 403].includes(response.status)) stop();
        throw new Error(typeof result.detail === "string" ? result.detail.slice(0, 300) : "保存失败（" + response.status + "）");
      }
      if (result.saved !== true || typeof result.duplicate !== "boolean") throw new Error("保存结果未确认，请先查看知识库");
      if (!valid(job)) return;
      if (result.duplicate) { state.duplicates++; row.status = result.item ? "已存在，未重复导入" : "此前保存的原资料已移除"; }
      else { state.saved++; row.status = "已保存，识别需另行同意"; }
      job.finished.set(row.name, stamp);
    } catch (error) {
      if (valid(job)) {
        row.status = error instanceof Error ? error.message : "保存未完成";
        if (sent) { state.failed++; row.status += "；未自动重试，请先核对知识库"; job.finished.set(row.name, stamp); }
      }
    } finally { job.controller = null; await handle.close(); }
  }
  async function scan(job) {
    if (!job || job.scanning) return;
    if (!valid(job)) { if (run === job) stop(true); return; }
    job.scanning = true;
    try {
      await sameDirectory(job);
      const entries = await fs.promises.readdir(job.folder, { withFileTypes: true });
      if (entries.length > 1000) throw new Error("目录超过 1000 个条目");
      const files = [], present = new Set();
      let attempted = 0;
      for (const entry of entries) {
        if (!valid(job)) return;
        if (!entry.isFile() || entry.isSymbolicLink()) continue;
        const stat = await fs.promises.lstat(path.join(job.folder, entry.name));
        if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink > 1) continue;
        const stamp = identity(stat), previous = job.observed.get(entry.name);
        present.add(entry.name);
        const row = { name: entry.name, size: stat.size, updatedAt: stat.mtimeMs, status: "仅本地观察" };
        if (job.mode === "import") {
          row.status = "等待文件稳定";
          if (!supported.test(entry.name) || entry.name.length > 200 || entry.name.startsWith(".")) row.status = "文件名或格式不支持，未上传";
          else if (!stat.size || stat.size > job.maxBytes) row.status = "文件为空或超过后台上限，未上传";
          else if (job.finished.get(entry.name) === stamp) row.status = job.outcomes.get(entry.name) || "已处理";
          else if (previous?.stamp === stamp && now() - previous.seen >= settleMs && attempted < 20) {
            attempted++; await upload(job, row, stamp); job.outcomes.set(entry.name, row.status);
          }
        }
        job.observed.set(entry.name, previous?.stamp === stamp ? previous : { stamp, seen: now() });
        files.push(row);
      }
      for (const name of job.observed.keys()) if (!present.has(name)) { job.observed.delete(name); job.finished.delete(name); job.outcomes.delete(name); }
      if (valid(job)) state = { ...state, files: files.sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 100),
        message: job.mode === "import" ? "正在导入此目录内稳定的支持文件，不含子目录；原件保存后需自行核对，付费识别不会自动启动。" : "只观察文件名、大小和修改时间，不读取内容、不上传。" };
    } catch {
      if (run === job) { stop(); state.message = "目录不可读、已变化或超过上限，已停止。请核对已保存资料后重新选择专用目录。"; }
    } finally { job.scanning = false; }
  }
  ipcMain.handle("knowledge:watch-status", event => { trusted(event); if (run && !valid(run)) stop(true); return state; });
  ipcMain.handle("knowledge:watch-stop", event => { trusted(event); return stop(); });
  ipcMain.handle("knowledge:watch-start", async (event, options) => {
    trusted(event);
    if (starting) throw new Error("正在选择目录，请完成当前操作");
    starting = true;
    try {
      const session = readSession();
      if (!session?.accessToken || !session.backendUrl || !(Date.parse(session.expiresAt) > now())) throw new Error("请先登录本人账号");
      const backend = new URL(session.backendUrl);
      if (!["http:", "https:"].includes(backend.protocol) || backend.username || backend.password || backend.search || backend.hash) throw new Error("后台地址无效，请重新登录");
      const mode = options?.autoImport === true ? "import" : "observe";
      const selected = await dialog.showOpenDialog(getWindow(), { title: mode === "import" ? "选择知识库专用导入文件夹" : "选择本地观察文件夹", properties: ["openDirectory"] });
      if (selected.canceled || !selected.filePaths[0]) return state;
      const chosen = path.resolve(selected.filePaths[0]), stat = await fs.promises.lstat(chosen);
      if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("请选择实际目录，不支持目录链接");
      const folder = await fs.promises.realpath(chosen);
      if (folder === path.parse(folder).root || folder.toLowerCase() === app.getPath("home").toLowerCase()) throw new Error("请选择专用文件夹，不支持磁盘根目录或用户主目录");
      if (mode === "import") {
        const answer = await dialog.showMessageBox(getWindow(), { type: "question", title: "开启文件夹自动导入", defaultId: 1, cancelId: 1,
          buttons: ["同意并开启导入", "取消"], message: "将现有及新增、修改后的支持文件上传到本人知识库？",
          detail: "目录：" + folder + "\n接收后台：" + session.backendUrl + "\n账号：" + (session.user?.username || "当前本人账号") +
            "\n\n只处理此目录，不含子目录；相同文件去重，修改内容另存新资料。停止、退出账号或应用即结束，不随重启自动开启，不删除本地文件，不自动调用云端识别。" +
            (backend.protocol === "http:" ? "\n当前后台使用 HTTP，传输不加密，请确认该地址可信。" : "") });
        if (answer.response !== 0) return state;
      }
      if (readSession()?.accessToken !== session.accessToken || readSession()?.backendUrl !== session.backendUrl) throw new Error("选择期间账号已改变，未开启导入");
      let maxBytes = 500 * 1024 * 1024;
      if (mode === "import") {
        const result = await fetchImpl(session.backendUrl.replace(/\/+$/, "") + "/api/v1/knowledge/capabilities", {
          headers: { Authorization: "Bearer " + session.accessToken }, redirect: "error", signal: AbortSignal.timeout(15000) });
        if (!result.ok) throw new Error("无法确认本人知识库后台，请检查登录和服务版本");
        const caps = await result.json();
        if (!(Number.isFinite(caps.max_upload_mb) && caps.max_upload_mb > 0)) throw new Error("后台尚不支持文件夹导入，请先升级知识库后台");
        maxBytes = Math.min(500, caps.max_upload_mb) * 1024 * 1024;
      }
      if (readSession()?.accessToken !== session.accessToken || readSession()?.backendUrl !== session.backendUrl || !(Date.parse(session.expiresAt) > now())) throw new Error("账号状态已改变，未开启导入");
      stop(true);
      const job = { folder, token: session.accessToken, backend: session.backendUrl, mode, maxBytes, timer: null, controller: null, scanning: false,
        observed: new Map(), finished: new Map(), outcomes: new Map() };
      run = job; state = { ...initial(), running: true, mode, folder, backend: session.backendUrl, message: "正在检查目录…" };
      await scan(job);
      if (valid(job)) { job.timer = setInterval(() => void scan(job), 5000); job.timer.unref?.(); }
      return state;
    } finally { starting = false; }
  });
  return { stop, scan: () => scan(run) };
};
