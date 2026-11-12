"use strict";

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");

const windowsOnly = { skip: process.platform !== "win32" };
const script = path.resolve(__dirname, "../build/video-runtime-update.ps1");

function fixture(t) {
  const temporary = fs.realpathSync(os.tmpdir());
  const root = fs.mkdtempSync(path.join(temporary, "signaldesk-video-update-"));
  t.after(() => {
    assert.equal(path.dirname(fs.realpathSync(root)), temporary);
    fs.rmSync(root, { recursive: true });
  });
  const install = path.join(root, "mai'yi desktop");
  const runtime = path.join(install, "resources", "video-runtime");
  const staged = `${install}.signaldesk-video-runtime`;
  const contents = Buffer.from("verified runtime payload");
  const manifest = { schema: 2, kind: "maiyi-bundled-video-runtime", files: [
    { path: "python/python.exe", bytes: contents.length, sha256: createHash("sha256").update(contents).digest("hex") },
  ] };
  const trustedManifest = path.join(root, "candidate-manifest.json");
  fs.writeFileSync(trustedManifest, JSON.stringify(manifest));
  function installRuntime() {
    fs.mkdirSync(path.join(runtime, "python"), { recursive: true });
    fs.writeFileSync(path.join(runtime, "python", "python.exe"), contents);
    fs.writeFileSync(path.join(runtime, "manifest.json"), JSON.stringify({ ...manifest, built_at: "different build timestamp" }));
  }
  function run(mode, status) {
    const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
      `& ([ScriptBlock]::Create([IO.File]::ReadAllText($env:SIGNALDESK_RUNTIME_SCRIPT))) ${mode}`], {
      env: { ...process.env, SIGNALDESK_RUNTIME_SCRIPT: script, SIGNALDESK_RUNTIME_INSTALL: install,
        SIGNALDESK_RUNTIME_MANIFEST: trustedManifest }, encoding: "utf8", windowsHide: true, timeout: 15_000,
    });
    assert.ifError(result.error);
    assert.equal(result.status, status, result.stderr || result.stdout);
  }
  return { installRuntime, run, runtime, staged, root, binary: path.join(runtime, "python", "python.exe") };
}

test("keeps the same runtime files across repeated preparation and installer restoration", windowsOnly, (t) => {
  const { installRuntime, run, binary, runtime, staged } = fixture(t);
  installRuntime();
  const before = fs.statSync(binary, { bigint: true });
  run("Prepare", 0);
  assert(!fs.existsSync(runtime));
  assert(fs.existsSync(staged));
  run("Prepare", 0); // A retry recovers the interrupted staging operation.
  run("Restore", 0);
  run("Verify", 0);
  const after = fs.statSync(binary, { bigint: true });
  assert.equal(after.ino, before.ino);
  assert.equal(after.mtimeNs, before.mtimeNs);
  assert(!fs.existsSync(staged));
});

test("requests extraction when the runtime is missing or has a same-size damaged file", windowsOnly, (t) => {
  const { installRuntime, run, binary, staged } = fixture(t);
  run("Prepare", 2);
  installRuntime();
  fs.writeFileSync(binary, Buffer.alloc(fs.statSync(binary).size, 65));
  run("Prepare", 2);
  run("Verify", 1);
  assert(!fs.existsSync(staged));
});

test("rejects a linked runtime directory before the old uninstaller runs", windowsOnly, (t) => {
  const { installRuntime, run, runtime, root } = fixture(t);
  installRuntime();
  const junction = path.join(runtime, "linked");
  const destination = path.join(root, "other-data");
  fs.mkdirSync(destination);
  fs.symlinkSync(destination, junction, "junction");
  try { run("Prepare", 1); }
  finally { fs.unlinkSync(junction); }
  assert(fs.existsSync(destination));
});
