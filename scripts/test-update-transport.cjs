"use strict";

// Real electron-updater transport and checksum test. Never executes an installer.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const http = require("node:http");
const crypto = require("node:crypto");
const { NsisUpdater } = require("electron-updater/out/NsisUpdater");
const { ElectronHttpExecutor } = require("electron-updater/out/electronHttpExecutor");
const { NodeHttpExecutor } = require("builder-util/out/nodeHttpExecutor");
const { createDesktopUpdater } = require("../electron/desktop-updater.cjs");

const OUTPUT_PARENT = "D:/maiyi/tmp";
const EVIDENCE = "D:/maiyi/docs/delivery/maiyi-autoupdate-release-20261023/evidence";
const VERSION = "0.5.4";
const ASSET = `SignalDesk-Setup-${VERSION}.exe`;
const fixtureBytes = Buffer.from("SYNTHETIC TRANSPORT TEST - NOT A WINDOWS EXECUTABLE\n".repeat(8192));
const digest = (bytes) => crypto.createHash("sha512").update(bytes).digest("base64");
const correctDigest = digest(fixtureBytes);
const incorrectDigest = digest(Buffer.from("deliberately different fixture"));

function yamlFor(sha512) {
  return `version: ${VERSION}\nfiles:\n  - url: ${ASSET}\n    sha512: '${sha512}'\n    size: ${fixtureBytes.length}\npath: ${ASSET}\nsha512: '${sha512}'\nreleaseDate: '2026-10-23T00:00:00.000Z'\n`;
}

class IsolatedAppAdapter {
  constructor(directory) {
    this.version = "0.5.3";
    this.name = "maiyi-update-transport-test";
    this.isPackaged = true;
    this.appUpdateConfigPath = path.join(directory, "app-update.yml");
    this.userDataPath = path.join(directory, "user-data");
    this.baseCachePath = path.join(directory, "cache");
    this.quitHandlers = [];
    this.quitCalls = 0;
    this.relaunchCalls = 0;
  }
  async initialize() {
    await fs.mkdir(this.userDataPath, { recursive: true });
    await fs.writeFile(this.appUpdateConfigPath, "updaterCacheDirName: isolated-update-cache\n", "utf8");
  }
  whenReady() { return Promise.resolve(); }
  relaunch() { this.relaunchCalls++; }
  quit() { this.quitCalls++; this.emitOrdinaryQuit(); }
  onQuit(callback) { this.quitHandlers.push(callback); }
  emitOrdinaryQuit() { for (const callback of this.quitHandlers) callback(0); }
}

class LoopbackExecutor extends NodeHttpExecutor {
  constructor(port, requests) { super(); this.port = port; this.requests = requests; }
  createRequest(options, callback) {
    assert.equal(options.protocol, "http:", "The transport fixture must stay on loopback HTTP");
    assert.equal(options.hostname || options.host, "127.0.0.1", "External network requests are prohibited");
    assert.equal(Number(options.port), this.port, "Only the fixture server port is allowed");
    this.requests.push({ host: "127.0.0.1", path: options.path, method: options.method || "GET" });
    // No proxy environment settings or external agents are used for the local fixture.
    return http.request({ ...options, agent: false }, callback);
  }
  download(url, destination, options) {
    // Use the production library's complete download and SHA512 verification path.
    return ElectronHttpExecutor.prototype.download.call(this, url, destination, options);
  }
}

async function scenario(kind, runDirectory, port) {
  const app = new IsolatedAppAdapter(path.join(runDirectory, kind));
  await app.initialize();
  const requests = [], transitions = [], errors = [];
  const nativeUpdater = new NsisUpdater(null, app);
  nativeUpdater.httpExecutor = new LoopbackExecutor(port, requests);
  nativeUpdater.disableDifferentialDownload = true;
  nativeUpdater.disableWebInstaller = true;
  let installAttempts = 0, spawnAttempts = 0, confirmationAttempts = 0;
  // Even a regression cannot launch the synthetic .exe during this test.
  nativeUpdater.doInstall = () => { installAttempts++; throw new Error("Fixture installation is disabled"); };
  nativeUpdater.spawnLog = () => { spawnAttempts++; throw new Error("Fixture process execution is disabled"); };
  nativeUpdater.spawnSyncLog = () => { spawnAttempts++; throw new Error("Fixture process execution is disabled"); };
  nativeUpdater.on("error", (error) => errors.push({ code: error.code || "UNKNOWN" }));
  const controller = createDesktopUpdater({
    app: { isPackaged: true, getVersion: () => app.version },
    dialog: { showMessageBox: async () => { confirmationAttempts++; throw new Error("No user installation is requested"); } },
    autoUpdater: nativeUpdater,
    sendState: (state) => transitions.push(state.status),
    canInstall: () => false,
  });
  // Only the transport test replaces the production feed with a local Generic feed.
  nativeUpdater.setFeedURL({ provider: "generic", url: `http://127.0.0.1:${port}/${kind}/` });
  assert.equal(nativeUpdater.autoDownload, true);
  assert.equal(nativeUpdater.autoInstallOnAppQuit, false);
  const result = await nativeUpdater.checkForUpdates();
  assert.ok(result?.downloadPromise, "Real updater must create its automatic download promise");
  assert.equal(result.updateInfo.version, VERSION);
  assert.equal(nativeUpdater.updateInfoAndProvider.provider.constructor.name, "GenericProvider");
  let downloadedFiles = [], rejectionCode = null;
  if (kind === "good") {
    downloadedFiles = await result.downloadPromise;
    assert.equal(downloadedFiles.length, 1);
    assert.equal(digest(await fs.readFile(downloadedFiles[0])), correctDigest);
    assert.equal(controller.getState().status, "ready");
    assert.ok(transitions.includes("downloading"));
    assert.ok(transitions.includes("ready"));
    assert.equal(nativeUpdater.quitHandlerAdded, false, "Ordinary exit must not register an install hook");
  } else {
    await assert.rejects(result.downloadPromise, (error) => {
      rejectionCode = error.code;
      return /CHECKSUM_MISMATCH/.test(String(error.code));
    });
    assert.equal(controller.getState().status, "error");
    assert.equal(transitions.includes("ready"), false);
    assert.equal(nativeUpdater.installerPath, null);
  }
  controller.stop();
  app.emitOrdinaryQuit();
  assert.equal(installAttempts, 0);
  assert.equal(spawnAttempts, 0);
  assert.equal(confirmationAttempts, 0);
  assert.equal(app.quitCalls, 0);
  assert.equal(app.relaunchCalls, 0);
  assert.equal(requests.length, 2);
  assert.ok(requests.some((request) => request.path.split("?")[0] === `/${kind}/latest.yml`));
  assert.ok(requests.some((request) => request.path.split("?")[0] === `/${kind}/${ASSET}`));
  assert.equal(JSON.stringify(requests).includes("api.github.com"), false);
  return { kind, status: "PASS", provider: "GenericProvider", requests, transitions,
    checksum: kind === "good" ? "SHA512 verified with exact downloaded fixture bytes" : "SHA512 mismatch rejected before ready",
    downloadedFiles, rejectionCode, errorCodes: errors, ordinaryQuitInstalled: false,
    installerExecuted: false, installationAttempts: installAttempts, processSpawnAttempts: spawnAttempts,
    nativeConfirmationAttempts: confirmationAttempts, updaterQuitHookAdded: nativeUpdater.quitHandlerAdded,
    signatureVerification: "N/A: synthetic bytes, isolated config intentionally has no publisherName; this test verifies SHA512 transport only" };
}

async function main() {
  const libraryVersion = require("electron-updater/package.json").version;
  assert.equal(libraryVersion, "6.8.9");
  await fs.mkdir(OUTPUT_PARENT, { recursive: true });
  await fs.mkdir(EVIDENCE, { recursive: true });
  const runDirectory = await fs.mkdtemp(path.join(OUTPUT_PARENT, "update-transport-"));
  await fs.writeFile(path.join(runDirectory, "synthetic-fixture.exe"), fixtureBytes);
  const serverRequests = [];
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, "http://127.0.0.1").pathname;
    serverRequests.push({ method: request.method, pathname });
    const match = /^\/(good|bad)\/(.+)$/.exec(pathname);
    if (!match) { response.writeHead(404); response.end(); return; }
    if (match[2] === "latest.yml") {
      response.writeHead(200, { "Content-Type": "text/yaml" });
      response.end(yamlFor(match[1] === "good" ? correctDigest : incorrectDigest));
    } else if (match[2] === ASSET) {
      response.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Length": fixtureBytes.length });
      response.end(fixtureBytes);
    } else { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  let report;
  try {
    const port = server.address().port;
    const good = await scenario("good", runDirectory, port);
    const bad = await scenario("bad", runDirectory, port);
    report = { status: "PASS", library: `electron-updater ${libraryVersion}`, executor: "NodeHttpExecutor + unmodified ElectronHttpExecutor.download",
      timestamp: new Date().toISOString(), runDirectory, fixtureBytes: fixtureBytes.length,
      fixtureIsExecutable: false, fixtureSha512: correctDigest, externalRequests: 0, githubApiRequests: 0,
      actualInstallerExecutions: 0, actualAppRestarts: 0, serverRequests, scenarios: [good, bad] };
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
  const evidencePath = path.join(EVIDENCE, "update-transport-report.json");
  await fs.writeFile(evidencePath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(JSON.stringify({ status: report.status, library: report.library, scenarios: report.scenarios.map(({ kind, status, rejectionCode }) => ({ kind, status, rejectionCode })), evidencePath, runDirectory, externalRequests: 0, actualInstallerExecutions: 0 }));
}

main().catch((error) => {
  console.error(JSON.stringify({ status: "FAIL", name: error.name, code: error.code || null, message: String(error.message) }));
  process.exitCode = 1;
});
