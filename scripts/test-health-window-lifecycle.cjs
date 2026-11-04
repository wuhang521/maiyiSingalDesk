// Execute the actual second-instance callback with window seams; no Electron/UI.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");

const sourcePath = path.resolve("electron/main.cjs");
const bytes = fs.readFileSync(sourcePath), source = bytes.toString("utf8");
const marker = 'app.on("second-instance", () => {';
const begin = source.indexOf(marker), end = source.indexOf("\n  });", begin);
assert.ok(begin >= 0 && end > begin);
const actualCallback = "() => {" + source.slice(begin + marker.length, end) + "\n}";
const cases = [], errors = [];
function runCase(name, initial, expected) {
  const actions = [], widget = { identity: "synthetic-widget", close() { throw Error("widget must remain untouched"); } };
  const fresh = () => ({ isDestroyed: () => false, isMinimized: () => false,
    restore: () => actions.push("restore"), show: () => actions.push("show"), focus: () => actions.push("focus") });
  const context = { mainWindow: typeof initial === "function" ? initial(actions) : initial, taskWidgetWindow: widget,
    createWindow() { actions.push("create"); context.mainWindow = fresh(); } };
  const callback = vm.runInNewContext(actualCallback, context);
  try {
    callback(); assert.deepEqual(actions, expected); assert.equal(context.taskWidgetWindow, widget);
    if (expected.includes("create")) { callback(); assert.deepEqual(actions, ["create", "show", "focus"]); }
    cases.push({ name, passed: true, actions, widget_preserved: true });
  } catch (error) { errors.push({ name, message: error.message }); cases.push({ name, passed: false, actions }); }
}
runCase("main closed while widget keeps the application alive", null, ["create"]);
runCase("destroyed main reference is replaced", () => ({ isDestroyed: () => true,
  isMinimized() { throw Error("destroyed window was accessed"); } }), ["create"]);
runCase("existing minimized main is restored and focused", actions => ({ isDestroyed: () => false, isMinimized: () => true,
  restore: () => actions.push("restore"), show: () => actions.push("show"), focus: () => actions.push("focus") }), ["restore", "show", "focus"]);
runCase("existing visible main is focused without recreation", actions => ({ isDestroyed: () => false, isMinimized: () => false,
  show: () => actions.push("show"), focus: () => actions.push("focus") }), ["show", "focus"]);

function callbackAfter(marker, endMarker) {
  const begin = source.indexOf(marker);
  if (begin < 0) return null;
  const end = source.indexOf(endMarker, begin + marker.length);
  assert.ok(end > begin, "Actual callback end must be found");
  return "(event) => {" + source.slice(begin + marker.length, end) + "\n}";
}
const closeCallback = callbackAfter('mainWindow.on("close", (event) => {', "\n  });");
const closedCallback = callbackAfter('mainWindow.on("closed", () => {', "\n  });");
const quitCallback = callbackAfter('app.on("before-quit", () => {', "});");
function closeCase(name, widget, explicitQuit, expectedMinimize) {
  const actions = [], webContents = { identity: "same-synthetic-renderer" };
  const main = { destroyed: false, webContents, minimize: () => actions.push("minimize") };
  const context = { mainWindow: main, taskWidgetWindow: widget, appIsQuitting: false,
    desktopUpdater: { stop: () => actions.push("updater-stop") },
    desktopVideo: { stop: () => actions.push("video-stop") },
    knowledgeWatch: { stop: value => actions.push("watch-stop:" + value) } };
  let prevented = false;
  try {
    if (explicitQuit) {
      vm.runInNewContext(quitCallback, context)();
    }
    if (closeCallback) vm.runInNewContext(closeCallback, context)({ preventDefault: () => { prevented = true; } });
    if (!prevented) { main.destroyed = true; vm.runInNewContext(closedCallback, context)(); }
    assert.equal(prevented, expectedMinimize);
    assert.equal(actions.includes("minimize"), expectedMinimize);
    if (expectedMinimize) {
      assert.equal(context.mainWindow, main); assert.equal(main.webContents, webContents); assert.equal(main.destroyed, false);
    } else { assert.equal(context.mainWindow, null); assert.equal(main.destroyed, true); }
    cases.push({ name, passed: true, prevented, actions, same_renderer_preserved: expectedMinimize });
  } catch (error) { errors.push({ name, message: error.message }); cases.push({ name, passed: false, prevented, actions }); }
}
closeCase("live widget keeps the actual Main renderer by minimizing normal close", { isDestroyed: () => false }, false, true);
closeCase("without widget normal close still destroys Main", null, false, false);
closeCase("destroyed widget does not prevent normal Main close", { isDestroyed: () => true }, false, false);
closeCase("explicit before-quit closes Main even with live widget", { isDestroyed: () => false }, true, false);
const result = { passed: errors.length === 0, cases, errors, source: sourcePath,
  source_sha256: crypto.createHash("sha256").update(bytes).digest("hex"), actual_callbacks: ["second-instance", "Main close", "Main closed", "before-quit"],
  mocks: ["BrowserWindow objects", "createWindow"], native_process_started: false, ui_started: false,
  real_profile_accessed: false, network_calls: 0 };
if (process.env.HEALTH_WINDOW_CONTRACT_OUTPUT) fs.writeFileSync(process.env.HEALTH_WINDOW_CONTRACT_OUTPUT, JSON.stringify(result, null, 2));
console.log(JSON.stringify(result));
if (!result.passed) process.exitCode = 1;
