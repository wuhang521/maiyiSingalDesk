const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const create = require("../electron/video-desktop.cjs");

function fixture(t, options = {}) {
  const temporaryRoot = os.tmpdir();
  fs.mkdirSync(temporaryRoot, { recursive: true });
  const root = fs.mkdtempSync(path.join(temporaryRoot, "maiyi-desktop-bridge-"));
  fs.mkdirSync(path.join(root, "server/scripts"), { recursive: true });
  fs.writeFileSync(path.join(root, "server/scripts/run_video_desktop.py"), "# test fixture");
  let expectedPython = process.execPath;
  const bundled = { schema: 2, platform: "win32", arch: "x64", python: "python/python.exe", ffmpeg: "tools/ffmpeg.exe" };
  if (options.bundled) {
    for (const relative of [bundled.python, bundled.ffmpeg]) {
      fs.mkdirSync(path.dirname(path.join(root, relative)), { recursive: true });
      fs.writeFileSync(path.join(root, relative), "runtime test placeholder");
    }
    expectedPython = fs.realpathSync(path.join(root, bundled.python));
  }
  if (!options.missing) fs.writeFileSync(path.join(root, "runtime.json"), JSON.stringify(options.manifest || (options.bundled ? bundled : { schema: 1, python: process.execPath })));
  let session = { backendUrl: "https://example.test/business", accessToken: "upstream-test-token" };
  let clock = 1000;
  const observed = { upstream: 0, leases: 0, updateChecks: 0, children: [], args: [], envelopes: [] };
  const net = { async fetch(url, init) {
    if (url.endsWith("/api/v1/auth/me")) {
      observed.upstream += 1;
      assert.equal(init.headers.Authorization, "Bearer " + session.accessToken);
      if (options.rejectIdentity) return { ok: false, status: 401 };
      return { ok: true, text: async () => JSON.stringify({ id: 7, username: "real-user", display_name: "真实用户" }) };
    }
    const envelope = observed.envelopes.at(-1);
    assert.ok(!init.headers.Authorization.includes("upstream-test-token"));
    if (url.endsWith("/desktop/update-safety")) {
      observed.updateChecks += 1;
      assert.equal(init.headers.Authorization, "Bearer " + envelope.control_token);
      if (options.safetyError) throw new Error("test connection failure");
      return { ok: !options.safetyRejected, json: async () => options.safetyInvalid ? {} : { safe_to_restart: options.safeToRestart !== false } };
    }
    if (url.endsWith("/desktop/lease")) {
      observed.leases += 1;
      assert.equal(init.headers.Authorization, "Bearer " + envelope.control_token);
      return { ok: true };
    }
    assert.equal(init.headers.Authorization, "Bearer " + envelope.capability_token);
    return { ok: true, json: async () => ({ service: "maiyi-desktop-video", instance_id: envelope.instance_id,
      identity_hash: options.wrongIdentity ? "invalid" : create.identityHash(envelope.identity.backend_url, envelope.identity.id) }) };
  } };
  function spawn(exe, args, env) {
    assert.equal(exe, expectedPython);
    assert.ok(!JSON.stringify({ args, env }).includes(session.accessToken));
    const child = new EventEmitter();
    Object.assign(child, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null,
      kill() { this.exitCode = 0; this.emit("exit", 0); } });
    child.stdin.once("data", data => {
      const envelope = JSON.parse(data.toString());
      observed.envelopes.push(envelope);
      assert.equal(Object.keys(envelope).length, 7);
      assert.ok(!data.toString().includes(session.accessToken));
      process.nextTick(() => child.stdout.write(JSON.stringify({ event: "ready", port: 38991, instance_id: envelope.instance_id }) + "\n"));
    });
    observed.children.push(child);
    observed.args.push(args);
    return child;
  }
  const bridge = create({ app: { isPackaged: options.packaged !== false, getPath: () => root, getAppPath: () => root },
    net, localFetch: net.fetch, readSession: () => session, runtimeRoot: root, spawnProcess: spawn, now: () => clock });
  t.after(() => {
    bridge.stop();
    assert.ok(path.resolve(root).startsWith(temporaryRoot + path.sep));
    fs.rmSync(root, { recursive: true, force: true });
  });
  return { bridge, observed, root, request: () => ({ backendUrl: session.backendUrl, accessToken: session.accessToken }),
    advance: ms => { clock += ms; }, setSession: next => { session = next; } };
}

test("development without embedded runtime keeps existing transport", async t => {
  const f = fixture(t, { missing: true, packaged: false });
  assert.equal(await f.bridge.connection(f.request()), null);
  assert.equal(f.observed.upstream, 0);
});
test("packaged runtime missing fails rather than silently using business video server", async t => {
  const f = fixture(t, { missing: true });
  await assert.rejects(f.bridge.connection(f.request()), /尚未安装完整/);
});
test("only current saved login can start the service", async t => {
  const f = fixture(t);
  await assert.rejects(f.bridge.connection({ backendUrl: "https://other.test", accessToken: "wrong" }), /登录状态已变化/);
  assert.equal(f.observed.children.length, 0);
});
test("upstream disabled identity cannot obtain video access", async t => {
  const f = fixture(t, { rejectIdentity: true });
  await assert.rejects(f.bridge.connection(f.request()), /失效或停用/);
  assert.equal(f.observed.children.length, 0);
});
test("concurrent requests verify once and launch one purpose-bound service", async t => {
  const f = fixture(t);
  const result = await Promise.all(Array.from({ length: 5 }, () => f.bridge.connection(f.request())));
  assert.equal(f.observed.children.length, 1);
  assert.equal(f.observed.upstream, 1);
  assert.ok(result.every(value => value.baseUrl === "http://127.0.0.1:38991" && value.accessToken === result[0].accessToken));
  assert.notEqual(result[0].accessToken, "upstream-test-token");
  const envelope = f.observed.envelopes[0];
  assert.equal(envelope.identity.username, "real-user");
  assert.match(envelope.data_dir, /video-production[/\\]accounts[/\\][a-f0-9]{64}$/);
});
test("longer sessions reverify upstream then renew with control token", async t => {
  const f = fixture(t);
  const first = await f.bridge.connection(f.request());
  f.advance(61000);
  const second = await f.bridge.connection(f.request());
  assert.equal(first.accessToken, second.accessToken);
  assert.equal(f.observed.upstream, 2);
  assert.equal(f.observed.leases, 1);
  assert.equal(f.observed.children.length, 1);
});
test("identity mismatch terminates the wrong service", async t => {
  const f = fixture(t, { wrongIdentity: true });
  await assert.rejects(f.bridge.connection(f.request()), /身份校验失败/);
  assert.equal(f.observed.children[0].exitCode, 0);
});
test("logout stops the service and rejects stale renderer requests", async t => {
  const f = fixture(t);
  const old = f.request();
  await f.bridge.connection(old);
  f.bridge.stop();
  f.setSession(null);
  await assert.rejects(f.bridge.connection(old), /登录状态已变化/);
  assert.equal(f.observed.children[0].exitCode, 0);
});
test("normalization rejects credentials and query tokens", () => {
  assert.equal(create.normalizeBackend("https://EXAMPLE.test:443/business/"), "https://example.test/business");
  assert.throws(() => create.normalizeBackend("https://user:password@example.test"));
  assert.throws(() => create.normalizeBackend("https://example.test?token=value"));
});

test("stale requests cannot stop a newer authenticated connection", async t => {
  const f = fixture(t);
  const request = f.request();
  const first = await f.bridge.connection(request);
  await assert.rejects(f.bridge.connection({ ...request, accessToken: "stale-token" }), /登录状态已变化/);
  assert.equal(f.observed.children[0].exitCode, null);
  assert.equal((await f.bridge.connection(request)).accessToken, first.accessToken);
});

test("schema 2 starts the bundled interpreter rather than a machine path", async t => {
  const f = fixture(t, { bundled: true });
  await f.bridge.connection(f.request());
  assert.equal(f.observed.children.length, 1);
  assert.deepEqual(f.observed.args[0], ["-I", "-B", "-X", "utf8", fs.realpathSync(path.join(f.root, "server/scripts/run_video_desktop.py"))]);
});

test("schema 2 rejects absolute, traversal, alternate stream and ambiguous paths", async t => {
  const f = fixture(t, { bundled: true });
  const base = { schema: 2, platform: "win32", arch: "x64", python: "python/python.exe", ffmpeg: "tools/ffmpeg.exe" };
  for (const value of [process.execPath, "/python/python.exe", "../python.exe", "python/../python.exe", "python\\python.exe", "python//python.exe", "./python.exe", "python/python.exe:stream", "python/\0.exe"]) {
    assert.throws(() => create.resolveRuntime(f.root, { ...base, python: value }), /路径无效/);
  }
  assert.throws(() => create.resolveRuntime(f.root, { ...base, platform: "darwin" }), /尚未安装完整/);
  assert.throws(() => create.resolveRuntime(f.root, { ...base, ffmpeg: "tools/missing.exe" }));
});

test("schema 2 rejects a linked runtime directory escaping the installation", async t => {
  const f = fixture(t, { bundled: true });
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "maiyi-outside-tools-"));
  t.after(() => fs.rmSync(outside, { recursive: true, force: true }));
  fs.writeFileSync(path.join(outside, "python.exe"), "outside placeholder");
  fs.symlinkSync(outside, path.join(f.root, "linked"), process.platform === "win32" ? "junction" : "dir");
  assert.throws(() => create.resolveRuntime(f.root, { schema: 2, platform: "win32", arch: "x64", python: "linked/python.exe", ffmpeg: "tools/ffmpeg.exe" }), /链接路径/);
});

test("schema 2 survives relocation into a new directory with spaces and Chinese characters", async t => {
  const f = fixture(t, { bundled: true });
  const destination = path.join(f.root, "迁移 安装目录");
  fs.mkdirSync(destination);
  for (const relative of ["python/python.exe", "tools/ffmpeg.exe", "server/scripts/run_video_desktop.py"]) {
    fs.mkdirSync(path.dirname(path.join(destination, relative)), { recursive: true });
    fs.copyFileSync(path.join(f.root, relative), path.join(destination, relative));
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(f.root, "runtime.json"), "utf8"));
  const resolved = create.resolveRuntime(destination, manifest);
  assert.equal(resolved.python, fs.realpathSync(path.join(destination, "python/python.exe")));
  assert.equal(resolved.ffmpeg, fs.realpathSync(path.join(destination, "tools/ffmpeg.exe")));
});

test("an inactive service permits installation without starting or verifying video", async t => {
  const f = fixture(t);
  assert.deepEqual(await f.bridge.canRestart(), { allowed: true });
  assert.equal(f.observed.upstream, 0);
  assert.equal(f.observed.children.length, 0);
});

test("restart uses the private control token and keeps a safe running service alive", async t => {
  const f = fixture(t);
  await f.bridge.connection(f.request());
  assert.deepEqual(await f.bridge.canRestart(), { allowed: true, message: "" });
  assert.equal(f.observed.updateChecks, 1);
  assert.equal(f.observed.children[0].exitCode, null);
});

test("active video tasks prevent update installation without stopping the service", async t => {
  const f = fixture(t, { safeToRestart: false });
  await f.bridge.connection(f.request());
  assert.deepEqual(await f.bridge.canRestart(), { allowed: false, message: "仍有视频任务正在进行，请稍后再安装。" });
  assert.equal(f.observed.children[0].exitCode, null);
});

for (const option of ["safetyError", "safetyRejected", "safetyInvalid"]) {
  test(`uncertain update safety fails closed: ${option}`, async t => {
    const f = fixture(t, { [option]: true });
    await f.bridge.connection(f.request());
    assert.deepEqual(await f.bridge.canRestart(), { allowed: false, message: "视频任务状态尚未确认，请稍后再安装。" });
    assert.equal(f.observed.children[0].exitCode, null);
  });
}
