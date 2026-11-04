const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const install = require("../electron/knowledge-watch.cjs");

function fixture(t, options = {}) {
  const base = path.resolve(process.env.KNOWLEDGE_TEST_TMP || path.join(__dirname, "../artifacts/knowledge-watch-tests"));
  fs.mkdirSync(base, { recursive: true });
  const root = fs.mkdtempSync(path.join(base, "run-"));
  const home = path.join(root, "home"), folder = path.join(root, "inbox");
  fs.mkdirSync(home); fs.mkdirSync(folder);
  const handlers = {}, window = { webContents: { mainFrame: {} } };
  const event = { sender: window.webContents, senderFrame: window.webContents.mainFrame };
  let session = { accessToken: "synthetic-token", backendUrl: "https://backend.example.test", expiresAt: new Date(Date.now() + 3600000).toISOString(), user: { username: "synthetic-member" } };
  let clock = Date.now();
  const observed = { prompts: [], uploads: [], requests: [] };
  const bridge = install({ app: { getPath: () => home }, ipcMain: { handle: (key, action) => { handlers[key] = action; } }, getWindow: () => window,
    dialog: { showOpenDialog: async () => ({ canceled: false, filePaths: [options.folder || folder] }),
      showMessageBox: async (_, prompt) => { observed.prompts.push(prompt); return { response: options.cancel ? 1 : 0 }; } },
    readSession: () => session, now: () => clock,
    fetchImpl: async (url, init) => {
      observed.requests.push(url);
      assert.equal(init.redirect, "error");
      assert.equal(init.headers.Authorization, "Bearer synthetic-token");
      if (url.endsWith("/capabilities")) return { ok: true, json: async () => ({ max_upload_mb: 1 }) };
      if (options.failure) throw new Error("synthetic network failure");
      const chunks = []; for await (const chunk of init.body) chunks.push(chunk);
      const body = Buffer.concat(chunks);
      assert.equal(crypto.createHash("sha256").update(body).digest("hex"), init.headers["X-Content-SHA256"]);
      assert.equal(body.length, Number(init.headers["Content-Length"]));
      assert.ok(!JSON.stringify(init.headers).includes(folder));
      observed.uploads.push({ body: body.toString(), key: init.headers["Idempotency-Key"], filename: Buffer.from(init.headers["X-Filename-Base64"], "base64").toString() });
      if (options.onUpload) await options.onUpload({ setSession: value => { session = value; }, bridge, signal: init.signal });
      return { ok: true, text: async () => JSON.stringify({ saved: true, duplicate: !!options.duplicate, item: { id: "test-item" } }) };
    } });
  t.after(() => { bridge.stop(true); assert.equal(path.dirname(root), base); fs.rmSync(root, { recursive: true, force: true }); });
  return { bridge, folder, observed, write: (name, text) => fs.writeFileSync(path.join(folder, name), text),
    start: autoImport => handlers["knowledge:watch-start"](event, { autoImport }),
    status: () => handlers["knowledge:watch-status"](event), stop: () => handlers["knowledge:watch-stop"](event),
    advance: () => { clock += 6000; }, setSession: value => { session = value; }, handlers, event };
}

test("automatic import requires directory and native backend/account consent", async t => {
  const f = fixture(t, { cancel: true }); f.write("note.txt", "synthetic original");
  assert.equal((await f.start(true)).running, false);
  assert.equal(f.observed.requests.length, 0);
  assert.match(f.observed.prompts[0].detail, /backend.example.test/);
  assert.match(f.observed.prompts[0].detail, /synthetic-member/);
  assert.equal(f.observed.prompts[0].defaultId, 1);
});
test("metadata observation reads no content and sends no network requests", async t => {
  const f = fixture(t); f.write("note.txt", "private synthetic original");
  await f.start(false); f.advance(); await f.bridge.scan();
  assert.equal(f.observed.requests.length, 0);
  assert.equal(f.status().files[0].name, "note.txt");
  assert.equal(f.status().mode, "observe");
});
test("stable files stream with exact checksum and unchanged files are not repeated", async t => {
  const f = fixture(t); f.write("中文测试.txt", "合成测试资料");
  await f.start(true); assert.equal(f.observed.uploads.length, 0);
  f.advance(); await f.bridge.scan(); await f.bridge.scan();
  assert.equal(f.observed.uploads.length, 1, JSON.stringify(f.status()));
  assert.equal(f.observed.uploads[0].body, "合成测试资料");
  assert.equal(f.status().saved, 1);
  f.write("中文测试.txt", "修改后的合成测试资料");
  await f.bridge.scan(); f.advance(); await f.bridge.scan();
  assert.equal(f.status().saved, 2);
  assert.notEqual(f.observed.uploads[0].key, f.observed.uploads[1].key);
});
test("restarting preserves content-based server idempotency", async t => {
  const f = fixture(t, { duplicate: true }); f.write("note.txt", "synthetic original");
  await f.start(true); f.advance(); await f.bridge.scan();
  const key = f.observed.uploads[0].key;
  f.stop(); await f.start(true); f.advance(); await f.bridge.scan();
  assert.equal(f.observed.uploads[1].key, key);
  assert.equal(f.status().duplicates, 1);
  assert.equal(f.status().saved, 0);
});
test("native voice aliases are imported as unchanged originals", async t => {
  const f = fixture(t);
  for (const name of ["voice.silk", "voice.slk", "voice.aud", "voice.amr"]) f.write(name, "synthetic voice original");
  await f.start(true); f.advance(); await f.bridge.scan();
  assert.deepEqual(f.observed.uploads.map(row => row.filename).sort(), ["voice.amr", "voice.aud", "voice.silk", "voice.slk"]);
  assert.ok(f.observed.uploads.every(row => row.body === "synthetic voice original"));
});
test("subfolders, hidden/unsupported and oversized files are excluded", async t => {
  const f = fixture(t);
  fs.mkdirSync(path.join(f.folder, "sub")); fs.writeFileSync(path.join(f.folder, "sub/secret.txt"), "not selected");
  f.write(".private.txt", "not selected"); f.write("script.exe", "not supported"); f.write("large.pdf", Buffer.alloc(1024 * 1024 + 1));
  await f.start(true); f.advance(); await f.bridge.scan();
  assert.equal(f.observed.uploads.length, 0);
  assert.equal(f.status().files.length, 3);
});
test("hardlinked files cannot import content outside selected folder", async t => {
  const f = fixture(t); const external = path.join(path.dirname(f.folder), "outside.txt");
  fs.writeFileSync(external, "outside synthetic secret"); fs.linkSync(external, path.join(f.folder, "linked.txt"));
  await f.start(true); f.advance(); await f.bridge.scan();
  assert.equal(f.observed.uploads.length, 0);
});
test("unknown upload results do not automatically replay", async t => {
  const f = fixture(t, { failure: true }); f.write("note.txt", "synthetic original");
  await f.start(true); f.advance(); await f.bridge.scan(); await f.bridge.scan();
  assert.equal(f.status().failed, 1);
  assert.equal(f.observed.requests.filter(url => url.endsWith("/capture/files")).length, 1);
  assert.match(f.status().files[0].status, /未自动重试/);
});
test("logout and explicit stop prevent subsequent file reads/uploads", async t => {
  const f = fixture(t); f.write("note.txt", "synthetic original");
  await f.start(true); f.setSession(null); f.advance(); await f.bridge.scan();
  assert.equal(f.status().running, false); assert.equal(f.status().folder, ""); assert.equal(f.observed.uploads.length, 0);
  await assert.rejects(f.start(true), /登录/);
});
test("untrusted frames are rejected before opening a directory", async t => {
  const f = fixture(t);
  await assert.rejects(f.handlers["knowledge:watch-start"]({ sender: {}, senderFrame: {} }, { autoImport: true }), /主工作台/);
  assert.throws(() => f.handlers["knowledge:watch-stop"]({ sender: {}, senderFrame: {} }), /主工作台/);
});
test("session change during upload discards private status and stops next files", async t => {
  const f = fixture(t, { onUpload: async ({ setSession }) => setSession(null) });
  f.write("a.txt", "first synthetic file"); f.write("b.txt", "second synthetic file");
  await f.start(true); f.advance(); await f.bridge.scan();
  assert.equal(f.observed.uploads.length, 1); assert.equal(f.status().running, false); assert.equal(f.status().folder, "");
});
