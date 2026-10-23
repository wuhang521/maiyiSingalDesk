// Desktop-owned video service. The business backend and saved login never change.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");

function normalizeBackend(value) {
  if (typeof value !== "string" || value.length > 2048) throw new Error("后台地址无效");
  const url = new URL(value.trim());
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error("后台地址无效");
  return url.href.replace(/\/+$/, "");
}

function identityHash(backend, id) {
  return crypto.createHash("sha256").update(`${backend}\0${id}`).digest("hex");
}

function bundledFile(root, value) {
  if (typeof value !== "string" || !value || value.includes("\\") || value.includes(":") || value.includes("\0") ||
      value.split("/").some(part => !part || part === "." || part === "..")) throw new Error("视频运行环境路径无效，请修复安装");
  const parent = fs.realpathSync(root);
  let target = parent;
  for (const part of value.split("/")) {
    target = path.join(target, part);
    if (fs.lstatSync(target).isSymbolicLink()) throw new Error("视频运行环境不允许链接路径，请修复安装");
  }
  const real = fs.realpathSync(target);
  const relative = path.relative(parent, real);
  if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative) || !fs.statSync(real).isFile()) {
    throw new Error("视频运行环境路径无效，请修复安装");
  }
  return real;
}

function resolveRuntime(root, manifest) {
  if (manifest.schema === 2 && manifest.platform === "win32" && manifest.arch === "x64") {
    return { python: bundledFile(root, manifest.python), ffmpeg: bundledFile(root, manifest.ffmpeg),
      runner: bundledFile(root, "server/scripts/run_video_desktop.py") };
  }
  // Existing local resource patches used schema 1 with an external interpreter.
  if (manifest.schema === 1 && typeof manifest.python === "string" && path.isAbsolute(manifest.python) && fs.statSync(manifest.python).isFile()) {
    return { python: manifest.python, runner: bundledFile(root, "server/scripts/run_video_desktop.py") };
  }
  throw new Error("本机视频运行环境尚未安装完整");
}

module.exports = function createDesktopVideo({ app, net, readSession, runtimeRoot, spawnProcess = spawn, localFetch = globalThis.fetch, now = Date.now }) {
  const root = runtimeRoot || (app.isPackaged ? path.join(process.resourcesPath, "video-runtime") : path.join(app.getAppPath(), "artifacts", "video-desktop-runtime"));
  let running = null;
  let queue = Promise.resolve();
  let generation = 0;

  function stop() {
    generation += 1;
    const previous = running;
    running = null;
    if (previous) {
      previous.child.stdin.destroy();
      if (previous.child.exitCode === null) previous.child.kill();
    }
  }

  function currentSession(request) {
    const session = readSession();
    if (!session?.accessToken || !session?.backendUrl || !request ||
        request.accessToken !== session.accessToken || normalizeBackend(request.backendUrl) !== normalizeBackend(session.backendUrl)) {
      throw new Error("登录状态已变化，请重新打开视频制作");
    }
    return session;
  }

  async function verifyIdentity(session) {
    const backend = normalizeBackend(session.backendUrl);
    let response;
    try {
      response = await net.fetch(`${backend}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${session.accessToken}` },
        redirect: "error", signal: AbortSignal.timeout(12000),
      });
    } catch { throw new Error("暂时无法验证当前账号，请检查迈医协同后台连接后重试"); }
    if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? "当前账号登录已失效或停用，请重新登录" : "账号验证服务暂时不可用");
    const body = await response.text();
    if (body.length > 65536) throw new Error("账号验证返回异常");
    let user;
    try { user = JSON.parse(body); } catch { throw new Error("账号验证返回异常"); }
    if (!Number.isSafeInteger(user.id) || user.id < 1 || typeof user.username !== "string" || !user.username.trim() ||
        typeof user.display_name !== "string" || user.username.length > 80 || user.display_name.length > 80) throw new Error("账号验证返回的身份不完整");
    return { backend_url: backend, id: user.id, username: user.username, display_name: user.display_name };
  }

  async function boot(identity, session, ticket) {
    const manifest = JSON.parse(fs.readFileSync(path.join(root, "runtime.json"), "utf8"));
    const runtime = resolveRuntime(root, manifest);
    const hash = identityHash(identity.backend_url, identity.id);
    const home = path.join(app.getPath("userData"), "video-production");
    const directory = path.join(home, "accounts", hash);
    fs.mkdirSync(directory, { recursive: true });
    const accessToken = crypto.randomBytes(32).toString("base64url");
    const controlToken = crypto.randomBytes(32).toString("base64url");
    const instance = crypto.randomUUID();
    const safeEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      /^(PATH|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMFILES|PROGRAMFILES\(X86\)|PATHEXT|SYSTEMDRIVE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i.test(key)));
    const child = spawnProcess(runtime.python, [...(manifest.schema === 2 ? ["-I", "-B", "-X", "utf8"] : []), runtime.runner], {
      cwd: root, env: { ...safeEnv, PYTHONIOENCODING: "utf-8", PYTHONDONTWRITEBYTECODE: "1" },
      windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    });
    const state = { child, accessToken, controlToken, identityHash: hash, identity,
      sessionToken: session.accessToken, verifiedAt: now(), baseUrl: "", ticket };
    running = state;
    child.stdin.on("error", () => {});
    // Preserve the parent pipe: EOF tells the service its owning desktop ended.
    child.stdin.write(JSON.stringify({ data_dir: directory, config_dir: path.join(home, "config"), identity,
      capability_token: accessToken, control_token: controlToken, instance_id: instance, cors_origins: ["null"] }) + "\n");
    child.stderr.on("data", () => {}); // Never surface provider credentials or upstream bodies in renderer errors.
    child.on("exit", () => { if (running === state) running = null; });
    const message = await new Promise((resolve, reject) => {
      const lines = createInterface({ input: child.stdout });
      const timer = setTimeout(() => finish(new Error("本机视频服务启动超时，请重试")), 45000);
      function finish(error, value) {
        clearTimeout(timer); lines.close(); child.off("error", failed); child.off("exit", exited);
        if (error) reject(error); else resolve(value);
      }
      function failed() { finish(new Error("本机视频服务启动失败，请检查本地运行环境")); }
      function exited() { finish(new Error("本机视频服务未就绪，请检查本地视频配置")); }
      child.once("error", failed); child.once("exit", exited);
      lines.on("line", line => {
        if (line.length > 4096) return;
        try { const value = JSON.parse(line); if (value.event === "ready" && value.instance_id === instance && Number.isInteger(value.port) && value.port > 0 && value.port <= 65535) finish(null, value); } catch {}
      });
    });
    if (ticket !== generation || running !== state) throw new Error("账号已切换，请重新打开视频制作");
    state.baseUrl = `http://127.0.0.1:${message.port}`;
    const response = await localFetch(state.baseUrl + "/desktop/status", {
      headers: { Authorization: `Bearer ${accessToken}` }, redirect: "error", signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error("本机视频服务连接失败");
    const status = await response.json();
    if (status.instance_id !== instance || status.identity_hash !== hash || status.service !== "maiyi-desktop-video") throw new Error("本机视频服务身份校验失败");
    return state;
  }

  async function connect(request) {
    if (!fs.existsSync(path.join(root, "runtime.json"))) {
      if (app.isPackaged) throw new Error("本机视频组件尚未安装完整，请修复迈医协同安装");
      return null;
    }
    const session = currentSession(request);
    const backend = normalizeBackend(session.backendUrl);
    if (running && running.sessionToken === session.accessToken && running.identity.backend_url === backend && running.baseUrl && now() - running.verifiedAt < 60000) {
      return { baseUrl: running.baseUrl, accessToken: running.accessToken, identityHash: running.identityHash };
    }
    const identity = await verifyIdentity(session);
    currentSession(request);
    const hash = identityHash(identity.backend_url, identity.id);
    if (running?.baseUrl && running.identityHash === hash && running.sessionToken === session.accessToken) {
      const response = await localFetch(running.baseUrl + "/desktop/lease", { method: "POST",
        headers: { Authorization: `Bearer ${running.controlToken}` }, redirect: "error", signal: AbortSignal.timeout(10000) });
      if (!response.ok) { stop(); throw new Error("本机视频连接已失效，请重试"); }
      running.verifiedAt = now();
      return { baseUrl: running.baseUrl, accessToken: running.accessToken, identityHash: hash };
    }
    stop();
    const state = await boot(identity, session, generation);
    currentSession(request);
    return { baseUrl: state.baseUrl, accessToken: state.accessToken, identityHash: state.identityHash };
  }

  return {
    async canRestart() {
      if (!running) return { allowed: true };
      const unknown = { allowed: false, message: "视频任务状态尚未确认，请稍后再安装。" };
      if (!running.baseUrl) return unknown;
      const state = running;
      try {
        const response = await localFetch(state.baseUrl + "/desktop/update-safety", {
          headers: { Authorization: `Bearer ${state.controlToken}` }, redirect: "error", signal: AbortSignal.timeout(5000),
        });
        if (!response.ok) return unknown;
        const status = await response.json();
        if (running !== state || typeof status?.safe_to_restart !== "boolean") return unknown;
        return { allowed: status.safe_to_restart === true,
          message: status.safe_to_restart ? "" : "仍有视频任务正在进行，请稍后再安装。" };
      } catch { return unknown; }
    },
    async connection(request) {
      // Serialize starts and account transitions. Recheck the saved session on every call.
      const operation = queue.then(() => connect(request)).catch(error => {
        // A stale renderer request must not tear down a newer account's service.
        try { currentSession(request); stop(); } catch {}
        throw error;
      });
      queue = operation.catch(() => {});
      return operation;
    },
    stop,
  };
};

module.exports.normalizeBackend = normalizeBackend;
module.exports.identityHash = identityHash;
module.exports.resolveRuntime = resolveRuntime;
