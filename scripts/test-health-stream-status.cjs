// Runs the actual main-process stream function with transport/window/timer seams.
// No Electron process, OS notification, real profile, or network is used here.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");

async function run() {
  const sourcePath = path.resolve("electron/main.cjs");
  const source = fs.readFileSync(sourcePath, "utf8");
  const begin = source.indexOf("async function streamSubscription(");
  const end = source.indexOf("\nfunction stopSubscription(", begin);
  assert.ok(begin >= 0 && end > begin);
  const actualFunction = source.slice(begin, end);
  const results = [];
  const config = { server: "http://127.0.0.1:1", topic: "health", token: "synthetic-only" };
  function harness(fetch) {
    const subscriptions = new Map(), events = [], timers = [];
    const controller = new AbortController(); subscriptions.set("health", controller);
    const context = { subscriptions, TextDecoder,
      net: { fetch }, topicUrl: (value, suffix) => value.server + "/" + value.topic + suffix,
      authHeaders: token => ({ Authorization: "Bearer " + token }), parseResponseError: async response => "HTTP" + response.status,
      mainWindow: { isDestroyed: () => false, webContents: { send: (name, event) => { assert.equal(name, "ntfy:event"); events.push(event); } } },
      setTimeout: (callback, delay) => { timers.push({ callback, delay }); },
    };
    const stream = vm.runInNewContext(actualFunction + "; streamSubscription", context);
    return { subscriptions, controller, events, timers, context, stream: () => stream("health", config, controller, 0) };
  }
  const response = () => new Response(JSON.stringify({ event: "message", topic: "health", id: "one", time: 1, message: "synthetic" }) + "\n");
  for (const status of [401, 502]) {
    const test = harness(async () => new Response("synthetic", { status }));
    await test.stream();
    assert.deepEqual(test.events.map(item => item.status), ["reconnecting"]);
    assert.equal(test.timers.length, 1); assert.equal(test.timers[0].delay, 1000);
    test.context.net.fetch = async () => response();
    await test.timers.shift().callback();
    assert.deepEqual(test.events.filter(item => item.type === "status").map(item => item.status), ["reconnecting", "connected", "reconnecting"]);
    assert.equal(test.events.filter(item => item.type === "message").length, 1);
    assert.equal(test.timers.length, 1);
    results.push({ case: "HTTP" + status + "-then-recovery", passed: true, connected_before_2xx: false, normal_eof_reconnect: true });
  }
  let fetches = 0;
  for (const kind of ["aborted", "retired"]) {
    const test = harness(async () => { fetches++; return response(); });
    if (kind === "aborted") test.controller.abort(); else test.subscriptions.set("health", new AbortController());
    await test.stream();
    assert.equal(test.events.length, 0); assert.equal(test.timers.length, 0);
    results.push({ case: kind + "-controller", passed: true });
  }
  assert.equal(fetches, 0);
  let resolveResponse;
  const replaced = harness(() => new Promise(resolve => { resolveResponse = resolve; }));
  const pending = replaced.stream();
  replaced.subscriptions.set("health", new AbortController()); resolveResponse(response()); await pending;
  assert.equal(replaced.events.length, 0); assert.equal(replaced.timers.length, 0);
  results.push({ case: "retired-while-response-pending", passed: true });
  const output = { passed: true, cases: results, actual_function: "streamSubscription", source: sourcePath,
    source_sha256: crypto.createHash("sha256").update(fs.readFileSync(sourcePath)).digest("hex"),
    mocks: ["net.fetch transport", "main window IPC receiver", "retry timer scheduler"], actual_node_response_stream: true,
    electron_launched: false, external_requests: 0, os_notifications: 0 };
  const destination = process.env.HEALTH_STREAM_CONTRACT_OUTPUT;
  if (destination) fs.writeFileSync(destination, JSON.stringify(output, null, 2));
  console.log(JSON.stringify(output));
}
run().catch(error => { console.error(error); process.exitCode = 1; });
