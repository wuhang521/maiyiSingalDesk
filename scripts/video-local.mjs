// Latest-source local trial. No production deployment or provider mocks. Private video-config.json is opt-in.
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { mkdir, readFile, writeFile, open, lstat } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const data = path.join(root, "artifacts", "video-local-trial");
try { if ((await lstat(path.dirname(data))).isSymbolicLink()) throw new Error("Refusing linked artifact directory"); }
catch (error) { if (error.code !== "ENOENT") throw error; }
await mkdir(data, { recursive: true });
if ((await lstat(data)).isSymbolicLink()) throw new Error("Refusing symlinked local trial directory");
const credentialsPath = path.join(data, "local-login.json");
const configurationPath = path.join(data, "video-config.json");
try {
  if ((await lstat(configurationPath)).isSymbolicLink()) throw new Error("Unsafe video configuration");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  const initial = JSON.parse(await readFile(path.join(root, "config/video-local.example.json"), "utf8"));
  const localPython = path.join(process.env.USERPROFILE || "", ".codex", "environments", "oldx-avatar", "Scripts", "python.exe");
  try { if ((await lstat(localPython)).isFile()) initial.TASK_VIDEO_PYTHON = localPython; } catch {}
  await writeFile(configurationPath, JSON.stringify(initial, null, 2), { flag: "wx", mode: 0o600 });
}
let login;
try {
  if ((await lstat(credentialsPath)).isSymbolicLink()) throw new Error("Unsafe credential file");
  login = JSON.parse(await readFile(credentialsPath, "utf8"));
  if (login.kind !== "video-local-trial" || typeof login.password !== "string" || login.password.length < 16) throw new Error("Unknown local credentials");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  login = { kind: "video-local-trial", admin: "local-admin", member: "local-member", password: randomBytes(18).toString("base64url") };
  await writeFile(credentialsPath, JSON.stringify(login, null, 2), { flag: "wx", mode: 0o600 });
}
const lock = createServer();
await new Promise((resolve, reject) => { lock.once("error", reject); lock.listen(18867, "127.0.0.1", resolve); });
async function freePort(preferred) {
  const server = createServer();
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(preferred, "127.0.0.1", resolve); });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
const apiPort = await freePort(18865);
const webPort = await freePort(5184);
const api = `http://127.0.0.1:${apiPort}`;
// Minimal OS/runtime environment, no .env credentials or inherited app config.
const safeEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  /^(PATH|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMFILES|PROGRAMFILES\(X86\)|PATHEXT|SYSTEMDRIVE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i.test(key)));
const children = [];
const logs = [];
let closing = false;
function stop(code = 0) {
  if (closing) return;
  closing = true;
  for (const child of children) if (child.exitCode === null) child.kill();
  lock.close();
  for (const log of logs) log.close().catch(() => {});
  setTimeout(() => process.exit(code), 500);
}
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
async function start(name, executable, args, env) {
  const log = await open(path.join(data, name + ".log"), "a");
  logs.push(log);
  const child = spawn(executable, args, { cwd: root, env: { ...safeEnv, ...env }, windowsHide: true, stdio: ["pipe", log.fd, log.fd] });
  children.push(child);
  child.once("error", error => { console.error(name + ": " + error.message); stop(1); });
  child.once("exit", code => { if (!closing) { console.log(name + " exited: " + code); stop(code === 0 && name === "desktop" ? 0 : 1); } });
  return child;
}
async function ready(url, label, validate) {
  const deadline = Date.now() + 25000;
  while (!closing && Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1500), redirect: "error" });
      if (response.status === 200 && await validate(response)) return;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 400));
  }
  throw new Error(label + " did not become ready; inspect local trial logs");
}
try {
  const python = path.resolve(root, "..", ".venv-signaldesk", "Scripts", "python.exe");
  const backend = await start("backend", python, ["server/scripts/run_video_local.py", data, String(apiPort)],
    { PYTHONIOENCODING: "utf-8", PYTHONDONTWRITEBYTECODE: "1" });
  backend.stdin.end(JSON.stringify({ password: login.password, frontend_port: webPort }) + "\n");
  await ready(api + "/local-trial/status", "Backend", async response => {
    const status = await response.json();
    return status.mode === "real-local-video" && status.production_connected === false && status.provider_simulation === false;
  });
  const runtimeStatus = await (await fetch(api + "/local-trial/status")).json();
  const frontend = await start("frontend", process.execPath,
    ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", String(webPort), "--strictPort", "--mode", "local-video"],
    { VITE_DEFAULT_BACKEND_URL: api, VITE_DEFAULT_NTFY_SERVER: api });
  frontend.stdin.end();
  const frontendUrl = `http://127.0.0.1:${webPort}`;
  await ready(frontendUrl, "Frontend", async response => (await response.text()).includes("/src/main.tsx"));
  const desktop = await start("desktop", path.join(root, "node_modules", "electron", "dist", "electron.exe"), [root],
    { VITE_DEV_SERVER_URL: frontendUrl, SIGNALDESK_E2E_USER_DATA: path.join(data, "profile"),
      SIGNALDESK_BACKEND_URL: api, SIGNALDESK_RELEASE_API_URL: api + "/local-trial/release" });
  desktop.stdin.end();
  await writeFile(path.join(data, "running.json"), JSON.stringify({ mode: "real-local-video", api, frontend: frontendUrl,
    supervisor_pid: process.pid, backend_pid: backend.pid, frontend_pid: frontend.pid, desktop_pid: desktop.pid, paid_enabled: runtimeStatus.paid_enabled }, null, 2));
  console.log("LOCAL TRIAL STARTED " + api + "; login details: " + credentialsPath);
  console.log("Hermes video source, isolated local account and project data. Configure video-config.json for providers; closing the desktop stops this local backend.");
} catch (error) {
  console.error(error.message);
  stop(1);
}
