import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { _electron as electron, expect } from "@playwright/test";
import { extractFile, listPackage } from "@electron/asar";

// Run explicitly against a prepared candidate EXE. No production login, profile,
// notification or business command is part of this test. OS opening is stubbed
// unless --open-real-browser-once explicitly enables the fixed destination once.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
process.chdir(root);
const argument = name => {
  const index = process.argv.indexOf(name);
  return index < 0 ? undefined : process.argv[index + 1];
};
const exeInput = argument("--exe") || process.env.SIGNALDESK_OFFICE_ENTRY_EXE;
const openRealBrowserOnce = process.argv.includes("--open-real-browser-once");
assert(exeInput, "Usage: node scripts/office-entry-e2e.mjs --exe <candidate/迈医协同.exe> [--output <project-directory>] [--open-real-browser-once]");
const executable = path.resolve(exeInput);
assert.equal(path.basename(executable), "迈医协同.exe");
assert((await fs.stat(executable)).isFile());
const output = path.resolve(argument("--output") || "docs/delivery/internal-management-entry-20261103/evidence/native");
const inside = (parent, child) => {
  const relative = path.relative(parent, child);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
};
assert(inside(root, output), "Evidence must remain within this source project");
for (let current = output; current !== path.dirname(current); current = path.dirname(current)) {
  try { assert(!(await fs.lstat(current)).isSymbolicLink(), "Refusing a linked evidence directory"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  if (current === root) break;
}
await fs.mkdir(output, { recursive: true });
const run = await fs.mkdtemp(path.join(output, "run-"));
const profile = path.join(run, "private-profile");
await fs.mkdir(profile);
const fixedUrl = "https://maiyitechmed.com/travel/";
const sha = bytes => createHash("sha256").update(bytes).digest("hex");
const readHash = async file => sha(await fs.readFile(file));
const archive = path.join(path.dirname(executable), "resources", "app.asar");
const checks = [], screenshots = [], pageErrors = [], unexpectedFixtureRequests = [], httpRequests = [];
const proxyBlocked = [];
let application, page, nativeState, sourceBinding, failure, currentCheck = "SETUP";
let applicationClosed = false, fixtureOrigin, ownedProcessExit = null;

function requireThat(condition, code) {
  if (!condition) throw Object.assign(new Error(code), { safeCode: code });
}
async function check(id, runCheck) {
  currentCheck = id;
  const started = Date.now();
  const actual = await runCheck();
  checks.push({ id, status: "PASS", elapsedMs: Date.now() - started, ...(actual ? { actual } : {}) });
  console.log(JSON.stringify({ type: "SAFE_PROGRESS", check: id, status: "PASS" }));
}

const syntheticUser = { id: 991, username: "entry-check", display_name: "入口验收员", staff_id: 991, role: "member" };
const fixture = createServer(async (request, response) => {
  const parsed = new URL(request.url, fixtureOrigin || "http://127.0.0.1");
  // Chromium receives a fail-closed proxy from its first process launch. This
  // server never forwards requests or establishes a tunnel to another host.
  if (fixtureOrigin && parsed.origin !== fixtureOrigin) {
    proxyBlocked.push({ scheme: parsed.protocol, method: request.method });
    response.writeHead(502); response.end(); return;
  }
  const headers = { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,X-SignalDesk-Operator",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS" };
  const reply = (value, status = 200) => { response.writeHead(status, headers); response.end(JSON.stringify(value)); };
  if (request.method === "OPTIONS") { reply({}); return; }
  const method = request.method, pathname = parsed.pathname;
  httpRequests.push({ method, path: pathname });
  if (method === "GET" && pathname === "/api/v1/auth/me") { reply(syntheticUser); return; }
  if (method === "GET" && pathname === "/api/v1/channels") { reply([]); return; }
  if (method === "GET" && pathname === "/api/v1/articles") { reply({ items: [], total: 0 }); return; }
  // The existing login lifecycle synchronizes its local empty synthetic cache.
  // This is a fixture-only no-op; no business records or real server are used.
  if (method === "POST" && pathname === "/api/v1/articles/sync") {
    let bytes = 0;
    for await (const block of request) bytes += block.length;
    if (bytes >= 4096) {
      unexpectedFixtureRequests.push({ method, path: pathname, reason: "UNEXPECTED_LARGE_FIXTURE_SYNC" });
      reply({ detail: "本地空缓存验收不接受大请求" }, 413); return;
    }
    reply({ accepted: 0 }); return;
  }
  unexpectedFixtureRequests.push({ method, path: pathname });
  reply({ detail: "本地入口验收不提供该操作" }, 405);
});
fixture.on("connect", (request, socket) => {
  proxyBlocked.push({ scheme: "CONNECT", method: request.method });
  socket.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n");
});
await new Promise(resolve => fixture.listen(0, "127.0.0.1", resolve));
fixtureOrigin = `http://127.0.0.1:${fixture.address().port}`;

try {
  await check("CANDIDATE_ARTIFACT_BINDING", async () => {
    const packaged = JSON.parse(extractFile(archive, "package.json"));
    const currentPackage = JSON.parse(await fs.readFile("package.json", "utf8"));
    const packedDist = Object.fromEntries(listPackage(archive)
      .map(name => name.replaceAll("\\", "/"))
      .filter(name => /^\/dist\/.+\.(?:html|js|css|png|webp|svg)$/.test(name))
      .map(name => [name.slice(1), sha(extractFile(archive, name.slice(1).replaceAll("/", path.sep)))]));
    requireThat(Object.keys(packedDist).length >= 4, "CANDIDATE_DIST_MISSING");
    for (const [file, digest] of Object.entries(packedDist))
      assert.equal(await readHash(path.join(root, file)), digest, "Candidate renderer must match current source build");
    sourceBinding = { version: packaged.version, executableSha256: await readHash(executable),
      archiveSha256: await readHash(archive), mainSha256: sha(extractFile(archive, "electron/main.cjs")),
      sidebarSourceSha256: await readHash("src/components/Sidebar.tsx"), dist: packedDist };
    assert.equal(packaged.version, currentPackage.version);
    assert.equal(sourceBinding.mainSha256, await readHash("electron/main.cjs"));
    return { version: packaged.version, distFiles: Object.keys(packedDist).length, sourceAndPackageMatched: true };
  });
  const env = { ...process.env, SIGNALDESK_E2E_USER_DATA: profile,
    SIGNALDESK_BACKEND_URL: fixtureOrigin, SIGNALDESK_RELEASE_API_URL: `${fixtureOrigin}/release-check` };
  delete env.ELECTRON_RUN_AS_NODE;
  currentCheck = "NATIVE_CANDIDATE_LAUNCH";
  application = await electron.launch({ executablePath: executable,
    args: [`--proxy-server=${fixtureOrigin}`, "--proxy-bypass-list=<-loopback>"], env, timeout: 30000 });
  page = await application.firstWindow({ timeout: 8000 });
  page.setDefaultTimeout(10000);
  page.on("pageerror", error => pageErrors.push({ type: error.name }));

  await check("ISOLATED_PROFILE_AND_FAIL_CLOSED_GUARDS", async () => {
    const info = await application.evaluate(async ({ app, ipcMain, session, shell }, config) => {
      const nodeRequire = typeof require === "function" ? require : process.mainModule.require.bind(process.mainModule);
      const req = nodeRequire("node:module").createRequire(app.getAppPath() + "/package.json");
      globalThis.__officeEntryHarness = { externalCalls: [], blockedRequests: [], notifications: 0,
        updateChecksSuppressed: 0, updateDownloads: 0, updateInstalls: 0, loginItemChanges: 0,
        realBrowserOpenRequests: 0, realBrowserOpenAccepted: 0, realBrowserOpenFailed: 0 };
      const state = globalThis.__officeEntryHarness;
      const updater = req("electron-updater").autoUpdater;
      updater.checkForUpdates = async () => { state.updateChecksSuppressed++; return null; };
      updater.downloadUpdate = async () => { state.updateDownloads++; throw new Error("TEST_DOWNLOAD_BLOCKED"); };
      updater.quitAndInstall = () => { state.updateInstalls++; throw new Error("TEST_INSTALL_BLOCKED"); };
      app.setLoginItemSettings = () => { state.loginItemChanges++; };
      app.getLoginItemSettings = () => ({ openAtLogin: false, wasOpenedAtLogin: false,
        wasOpenedAsHidden: false, restoreState: false, executableWillLaunchAtLogin: false, launchItems: [] });
      ipcMain.removeHandler("notification:show");
      ipcMain.handle("notification:show", () => { state.notifications++; return true; });
      // Keep the product's native setWindowOpenHandler. The default OS edge is
      // a recording stub. The opt-in permits only the first call, with the exact
      // fixed URL; subsequent keyboard clicks never launch a second browser.
      const originalOpenExternal = shell.openExternal.bind(shell);
      shell.openExternal = async url => {
        state.externalCalls.push(String(url));
        if (!config.openRealBrowserOnce || state.externalCalls.length !== 1 ||
          typeof url !== "string" || url !== config.fixedUrl) return;
        state.realBrowserOpenRequests++;
        try {
          await originalOpenExternal(url);
          state.realBrowserOpenAccepted++;
        } catch {
          state.realBrowserOpenFailed++;
          throw new Error("REAL_BROWSER_OPEN_REQUEST_REJECTED");
        }
      };
      session.defaultSession.webRequest.onBeforeRequest({ urls: ["http://*/*", "https://*/*"] }, (details, callback) => {
        const blocked = new URL(details.url).origin !== config.origin;
        if (blocked) state.blockedRequests.push({ type: details.resourceType });
        callback({ cancel: blocked });
      });
      return { userData: app.getPath("userData"), version: app.getVersion(),
        proxy: await session.defaultSession.resolveProxy("https://office-entry.invalid/") };
    }, { origin: fixtureOrigin, fixedUrl, openRealBrowserOnce });
    assert.equal(path.resolve(info.userData), path.resolve(profile));
    assert.equal(info.version, sourceBinding.version);
    requireThat(info.proxy.includes(new URL(fixtureOrigin).host), "STARTUP_FAIL_CLOSED_PROXY_NOT_ACTIVE");
    return { ownedProfileVerified: true, notificationOsEdgeStubbed: true,
      updaterDownloadsBlocked: true, externalBrowserOsEdge: openRealBrowserOnce ? "FIXED_URL_ONCE_REAL" : "STUBBED" };
  });

  await check("SYNTHETIC_AUTHENTICATED_WORKSPACE", async () => {
    await expect(page.getByRole("heading", { name: "登录工作台", exact: true })).toBeVisible();
    const syntheticSession = { backendUrl: fixtureOrigin, accessToken: "synthetic-office-entry-local-only",
      expiresAt: "2035-01-01T00:00:00Z", user: { id: syntheticUser.id, username: syntheticUser.username,
        displayName: syntheticUser.display_name, staffId: syntheticUser.staff_id, role: syntheticUser.role } };
    await page.evaluate(async session => {
      localStorage.setItem("signaldesk.channels.v1", "[]");
      localStorage.setItem("signaldesk.feed.v1", "[]");
      localStorage.setItem("signaldesk.trash.v1", "[]");
      localStorage.setItem("signaldesk.settings.v1", JSON.stringify({ notifications: false,
        notificationSound: false, launchAtLogin: false, globalAuthToken: "", operatorName: "入口验收员" }));
      await window.signalDesk.saveAuthSession(session);
    }, syntheticSession);
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.locator(".app-shell")).toBeVisible();
    await expect(page.getByRole("heading", { name: "今日情报", exact: true })).toBeVisible();
    requireThat(httpRequests.some(row => row.path === "/api/v1/auth/me"), "SYNTHETIC_AUTH_NOT_READ");
    return { realPreloadSessionStorage: true, server: "loopback synthetic fixture", productionAuthUsed: false };
  });

  await check("EXISTING_NAVIGATION_AND_SAFE_EXTERNAL_LINK", async () => {
    await expect(page.getByRole("button", { name: "任务中心", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "知识库", exact: true })).toBeVisible();
    const link = page.getByRole("link", { name: /内部管理系统/ });
    await expect(link).toHaveCount(1);
    await expect(link).toHaveAttribute("href", fixedUrl);
    await expect(link).toHaveAttribute("target", "_blank");
    const rel = (await link.getAttribute("rel") || "").split(/\s+/);
    requireThat(rel.includes("noopener") && rel.includes("noreferrer"), "EXTERNAL_LINK_REL_MISSING");
    const parsed = new URL(await link.getAttribute("href"));
    requireThat(!parsed.search && !parsed.hash && !parsed.username && !parsed.password, "UNEXPECTED_CREDENTIAL_OR_QUERY");
    return { existingTaskAndKnowledgeEntriesVisible: true, fixedDestinationWithoutToken: true };
  });

  const nativeWindow = await application.browserWindow(page);
  await page.getByRole("textbox", { name: "搜索信息", exact: true }).fill("本地入口验收");
  for (const [label, width, height, keyboard] of [["wide", 1440, 960, false], ["compact", 1080, 700, true]]) {
    await check(`${label.toUpperCase()}_NATIVE_EXTERNAL_OPEN_AND_CONTEXT`, async () => {
      await nativeWindow.evaluate((window, size) => window.setContentSize(size.width, size.height), { width, height });
      await expect.poll(() => page.evaluate(() => innerWidth)).toBe(width);
      const link = page.getByRole("link", { name: /内部管理系统/ });
      await expect(link).toBeVisible();
      await link.scrollIntoViewIfNeeded();
      const bounds = await link.evaluate(element => {
        const box = element.getBoundingClientRect();
        return { left: box.left, top: box.top, right: box.right, bottom: box.bottom,
          width: box.width, height: box.height, viewportWidth: innerWidth, viewportHeight: innerHeight };
      });
      requireThat(bounds.left >= 0 && bounds.top >= 0 && bounds.right <= bounds.viewportWidth + 1 &&
        bounds.bottom <= bounds.viewportHeight + 1 && bounds.height >= 32, "EXTERNAL_LINK_CLIPPED_OR_TOO_SMALL");
      const oldUrl = page.url();
      const activeLabel = await page.locator(".primary-nav .nav-button.active").innerText();
      const previousCount = await application.evaluate(() => globalThis.__officeEntryHarness.externalCalls.length);
      if (keyboard) { await link.focus(); await page.keyboard.press("Enter"); }
      else await link.click();
      await expect.poll(() => application.evaluate(() => globalThis.__officeEntryHarness.externalCalls.length))
        .toBe(previousCount + 1);
      const call = await application.evaluate(() => globalThis.__officeEntryHarness.externalCalls.at(-1));
      assert.equal(call, fixedUrl);
      assert.equal(page.url(), oldUrl);
      assert.equal(await page.locator(".primary-nav .nav-button.active").innerText(), activeLabel);
      await expect(page.getByRole("textbox", { name: "搜索信息", exact: true })).toHaveValue("本地入口验收");
      assert.equal(application.windows().length, 1, "The external link must not create an internal business BrowserWindow");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      requireThat(!overflow, "HORIZONTAL_OVERFLOW");
      const settingsInView = await page.locator('.sidebar-footer').getByRole('button', {name: '设置', exact: true})
        .evaluate(element => { const r = element.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight + 1; });
      requireThat(settingsInView, "EXISTING_SETTINGS_CONTROL_CLIPPED");
      const screenshot = path.join(run, `office-entry-${label}.png`);
      await page.screenshot({ path: screenshot });
      screenshots.push({ path: path.relative(root, screenshot).replaceAll(path.sep, "/"), sha256: await readHash(screenshot) });
      return { nativeContentViewport: { width, height }, link: bounds, keyboard, originalContextPreserved: true,
        nativeWindowOpenHandlerExercised: true, shellOpenExternalRecorded: true,
        realOsBrowserPageObserved: false };
    });
  }

  await check("NO_EXTERNAL_BUSINESS_REQUESTS_OR_SIDE_EFFECTS", async () => {
    if (openRealBrowserOnce) await expect.poll(() => application.evaluate(() =>
      globalThis.__officeEntryHarness.realBrowserOpenAccepted), { timeout: 15000 }).toBe(1);
    nativeState = await application.evaluate(() => globalThis.__officeEntryHarness);
    assert.deepEqual(nativeState.externalCalls, [fixedUrl, fixedUrl]);
    assert.equal(nativeState.blockedRequests.length, 0);
    assert.equal(proxyBlocked.length, 0);
    assert.equal(nativeState.notifications, 0);
    assert.equal(nativeState.updateDownloads, 0);
    assert.equal(nativeState.updateInstalls, 0);
    assert.equal(nativeState.loginItemChanges, 0);
    assert.equal(nativeState.realBrowserOpenRequests, openRealBrowserOnce ? 1 : 0);
    assert.equal(nativeState.realBrowserOpenAccepted, openRealBrowserOnce ? 1 : 0);
    assert.equal(nativeState.realBrowserOpenFailed, 0);
    assert.equal(unexpectedFixtureRequests.length, 0);
    assert.equal(pageErrors.length, 0);
    return { pageErrors: 0, unexpectedRequests: 0, notifications: 0, updaterDownloads: 0,
      businessCommandsToProduction: 0, originalUserProfileOpened: false,
      realOsBrowserOpenRequestAccepted: openRealBrowserOnce, realOsBrowserPageObserved: false };
  });
} catch (error) {
  failure = { check: currentCheck, errorType: error.name,
    code: /^[A-Z0-9_]+$/.test(error.safeCode || "") ? error.safeCode : "ASSERTION_FAILED" };
} finally {
  if (application) {
    try {
      // Preserve any real-open acknowledgement even when an unrelated later
      // layout assertion fails before the final side-effect check.
      nativeState = await application.evaluate(() => globalThis.__officeEntryHarness);
    } catch { /* A launch failure may not have installed the harness yet. */ }
    const child = application.process();
    let closeError = null;
    try { await application.close(); }
    catch (error) { closeError = {type: error.name, message: error.message.slice(0, 180)}; }
    try {
      await expect.poll(() => child.exitCode !== null || child.signalCode !== null,
        { timeout: 10000 }).toBe(true);
      ownedProcessExit = {pid: child.pid, exitCode: child.exitCode, signalCode: child.signalCode, closeError};
      requireThat(child.exitCode === 0 || child.signalCode === 'SIGTERM', "OWNED_APP_EXIT_NOT_CLEAN");
      applicationClosed = true;
    } catch {
      ownedProcessExit = {pid: child.pid, exitCode: child.exitCode, signalCode: child.signalCode, closeError};
      failure ||= { check: "OWNED_APP_CLEANUP", code: "OWNED_APP_CLOSE_FAILED" };
    }
  }
  fixture.closeAllConnections();
  await new Promise(resolve => fixture.close(resolve));
  if ((!application || applicationClosed) && inside(run, profile)) {
    try {
      requireThat(!(await fs.lstat(profile)).isSymbolicLink(), "OWNED_PROFILE_BECAME_LINKED");
      await fs.rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
    } catch { failure ||= { check: "OWNED_PROFILE_CLEANUP", code: "OWNED_PROFILE_REMOVE_FAILED" }; }
  }
}
const result = { at: new Date().toISOString(), status: failure ? "FAIL" : "PASS", checks,
  mode: openRealBrowserOnce ? "REAL_FIXED_BROWSER_REQUEST_ONCE" : "ISOLATED_OS_STUB",
  counts: { pass: checks.length, fail: failure ? 1 : 0 }, failure, sourceBinding, screenshots, pageErrors,
  fixtureRequests: httpRequests, unexpectedFixtureRequests, proxyBlockedCount: proxyBlocked.length,
  nativeCounters: nativeState ? { externalCalls: nativeState.externalCalls.length,
    blockedRequests: nativeState.blockedRequests.length, notifications: nativeState.notifications,
    updateChecksSuppressed: nativeState.updateChecksSuppressed, updateDownloads: nativeState.updateDownloads,
    updateInstalls: nativeState.updateInstalls, realBrowserOpenRequests: nativeState.realBrowserOpenRequests,
    realBrowserOpenAccepted: nativeState.realBrowserOpenAccepted, realBrowserOpenFailed: nativeState.realBrowserOpenFailed } : null,
  ownedApplicationClosed: applicationClosed, ownedProcessExit, originalUserProfileRead: false,
  productionBusinessApiAccessedByFixture: false,
  productionBackendAccessed: openRealBrowserOnce ? "NOT_INSPECTED_SYSTEM_BROWSER" : false,
  realOsBrowserOpenRequestAccepted: nativeState?.realBrowserOpenAccepted === 1,
  realOsBrowserPageObserved: false,
  limitation: openRealBrowserOnce
    ? "The first exact fixed-URL menu click invokes the saved original shell.openExternal once; Promise acceptance is recorded, while the real browser page/network and existing browser login are not inspected. Remaining clicks use the recording stub. Authentication and backend fixture remain synthetic and isolated."
    : "Real Electron window-open handler and preload session storage with a synthetic loopback backend; OS openExternal is a recording stub. This does not prove an actual external browser launch or production login.",
};
await fs.writeFile(path.join(run, "results.json"), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ status: result.status, counts: result.counts, failure,
  report: path.relative(root, path.join(run, "results.json")).replaceAll(path.sep, "/") }));
if (failure) process.exitCode = 1;
