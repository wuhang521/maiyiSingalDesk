import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { _electron as electron, expect } from "@playwright/test";

const require = createRequire(import.meta.url);
const { load } = require("js-yaml");
const project = process.cwd();
const release = path.join(project, "release", "video-runtime-reuse-v0.6.7");
const evidence = path.join(project, "artifacts", "video-runtime-reuse-0.6.7");
const mode = process.argv[2] || "local-download";
assert(["local-download", "local-install", "production-download"].includes(mode));
const actualInstall = mode === "local-install";
const official = mode === "production-download";
const previous = actualInstall ? path.resolve(project, "..", "signaldesk-desktop")
  : path.join(project, "release", "channel-management-v0.6.5", "win-unpacked");
const newApp = path.join(release, "win-unpacked", "resources", "app.asar");
const installedApp = path.join(previous, "resources", "app.asar");
const runtime = path.join(previous, "resources", "video-runtime");
const usePreviousPackagedApp = actualInstall && process.env.SIGNALDESK_UPDATE_RECHECK === "1";
const oldAppDirectory = usePreviousPackagedApp ? path.join(project, "release", "wechat-pairing-v0.6.6", "win-unpacked") : previous;
const executable = path.join(oldAppDirectory, "迈医协同.exe");
const manifest = await readFile(path.join(release, "latest.yml"));
const update = load(manifest);
const version = update.version;
const name = `SignalDesk-Setup-${version}.exe`;
const installer = path.join(release, name);

async function hash(file, algorithm = "sha256", encoding = "hex") {
  const result = createHash(algorithm);
  for await (const chunk of createReadStream(file)) result.update(chunk);
  return result.digest(encoding);
}

async function sampleRuntime() {
  const runtimeManifest = JSON.parse(await readFile(path.join(runtime, "runtime.json"), "utf8"));
  return Object.fromEntries(await Promise.all(["manifest.json", runtimeManifest.python, runtimeManifest.ffmpeg].map(async (file) => {
    const metadata = await stat(path.join(runtime, file), { bigint: true });
    return [file, { fileId: String(metadata.ino), modified: String(metadata.mtimeNs) }];
  })));
}

assert.equal(update.files[0].size, (await stat(installer)).size);
assert.equal(await hash(installer, "sha512", "base64"), update.sha512);
await mkdir(evidence, { recursive: true });
const attempt = await mkdtemp(path.join(evidence, `${mode}-`));
const requests = [];
const server = createServer((request, response) => {
  const pathname = new URL(request.url, "http://127.0.0.1").pathname;
  requests.push(pathname);
  if (pathname === "/latest.yml") {
    response.writeHead(200, { "Content-Length": manifest.length }).end(manifest);
  } else if (pathname === `/${name}`) {
    response.writeHead(200, { "Content-Length": update.files[0].size, "Content-Type": "application/octet-stream" });
    createReadStream(installer).pipe(response);
  } else {
    response.writeHead(404).end();
  }
});
await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
const localhost = `http://127.0.0.1:${server.address().port}/`;
const cache = path.join(attempt, "cache");
const startMarker = path.join(attempt, "installer-start.json");
const env = { ...process.env, SIGNALDESK_E2E_USER_DATA: path.join(attempt, "profile"), SIGNALDESK_BACKEND_URL: localhost };
delete env.ELECTRON_RUN_AS_NODE;
delete env.VITE_DEV_SERVER_URL;
const before = actualInstall ? await sampleRuntime() : null;
const previousAppId = actualInstall ? (await stat(installedApp, { bigint: true })).ino : null;
const expectedAppHash = actualInstall ? await hash(newApp) : null;
if (actualInstall) await writeFile(path.join(attempt, "before.json"), JSON.stringify(before, null, 2) + "\n");
let client;
try {
  client = await electron.launch({ executablePath: executable, env, timeout: 30_000 });
  const page = await client.firstWindow();
  const settings = await client.evaluate(async ({ app, dialog }, options) => {
    const { autoUpdater } = process.mainModule.require("electron-updater");
    Object.defineProperty(autoUpdater.app, "baseCachePath", { value: options.cache });
    if (!options.official) {
      autoUpdater.setFeedURL({ provider: "generic", url: options.localhost });
      autoUpdater.disableDifferentialDownload = true;
    }
    if (options.actualInstall) {
      autoUpdater.installDirectory = options.previous;
      const quitAndInstall = autoUpdater.quitAndInstall.bind(autoUpdater);
      dialog.showMessageBox = async () => ({ response: 1 });
      autoUpdater.quitAndInstall = (silent, restart) => {
        process.mainModule.require("node:fs").writeFileSync(options.startMarker,
          JSON.stringify({ startedAt: Date.now(), requestedSilent: silent, requestedRestart: restart }));
        // The application's real confirmation and IPC flow calls the installer. Run the
        // wizard silently during the automated test and reopen the real profile separately.
        quitAndInstall(true, false);
      };
    }
    globalThis.signalDeskUpdateResult = new Promise((resolve, reject) => {
      autoUpdater.once("update-downloaded", (value) => resolve({ version: value.version, file: value.downloadedFile }));
      autoUpdater.once("error", reject);
    });
    return { from: app.getVersion(), autoDownload: autoUpdater.autoDownload,
      ordinaryQuitInstalls: autoUpdater.autoInstallOnAppQuit };
  }, { cache, official, localhost, actualInstall, previous, startMarker });
  assert.equal(settings.from, actualInstall ? "0.6.6" : "0.6.5");
  assert(settings.autoDownload && !settings.ordinaryQuitInstalls);
  await expect.poll(() => page.evaluate(() => window.signalDesk.getUpdateState().then((value) => value.status)),
    { timeout: 150_000 }).toBe("ready");
  const downloaded = await client.evaluate(() => globalThis.signalDeskUpdateResult);
  assert.equal(downloaded.version, version);
  assert(path.resolve(downloaded.file).startsWith(cache + path.sep));
  assert.equal(await hash(downloaded.file, "sha512", "base64"), update.sha512);
  const result = { passed: true, mode, from: settings.from, to: version, installerBytes: update.files[0].size,
    installerSha512: update.sha512, automaticCheckAndDownload: true, requests };
  if (actualInstall) {
    await page.evaluate(() => { void window.signalDesk.installUpdate(); }).catch(() => undefined);
    await expect.poll(async () => stat(startMarker).then(() => true, () => false), { timeout: 15_000 }).toBe(true);
    const marker = JSON.parse(await readFile(startMarker, "utf8"));
    assert.equal(marker.requestedSilent, false);
    assert.equal(marker.requestedRestart, true);
    let actual;
    const deadline = Date.now() + 240_000;
    while (Date.now() < deadline) {
      try {
        const currentApp = await stat(installedApp, { bigint: true });
        if (currentApp.ino !== previousAppId && await hash(installedApp) === expectedAppHash) {
          actual = await sampleRuntime();
          break;
        }
      } catch (error) {
        if (error.code !== "ENOENT" && error.code !== "EBUSY") throw error;
      }
      await sleep(500);
    }
    assert.deepEqual(actual, before, "The installed runtime must contain the same files, not newly extracted copies");
    Object.assign(result, { installed: true, runtimeRetained: true, installSeconds: (Date.now() - marker.startedAt) / 1000,
      runtimeBefore: before, runtimeAfter: actual, actualInstallDirectory: previous, usePreviousPackagedApp });
    client = null;
  }
  await writeFile(path.join(evidence, `${mode}.json`), JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result));
} finally {
  if (client) await client.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
