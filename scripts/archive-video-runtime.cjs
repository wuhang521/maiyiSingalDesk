"use strict";

const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { getPath7za } = require("app-builder-lib/out/toolsets/7zip");

async function main() {
  const project = path.resolve(__dirname, "..");
  const artifacts = path.join(project, "artifacts");
  const source = path.resolve(process.argv[2] || path.join(artifacts, "bundled-video-runtime"));
  const output = path.join(artifacts, "video-runtime-installer");
  if (path.dirname(source) !== artifacts || source === output) throw new Error("Expected a bundled runtime under artifacts");
  execFileSync("python", ["-B", "scripts/package-video-runtime.py", "--verify-only", "--output", source], {
    cwd: project, stdio: "inherit", windowsHide: true,
  });
  fs.mkdirSync(output, { recursive: true });
  if (fs.realpathSync(output) !== output) throw new Error("Linked installer artifact directory");
  const temporary = fs.mkdtempSync(path.join(output, "build-"));
  const archive = path.join(temporary, "video-runtime.7z");
  try {
    const archiveTool = await getPath7za();
    execFileSync(archiveTool, ["a", "-t7z", "-mx=5", "-m0=LZMA2", "-mmt=2", "-mtm=off", "-mta=off", "-mtc=off", archive, "*"], {
      cwd: source, stdio: "inherit", windowsHide: true,
    });
    execFileSync(archiveTool, ["t", archive], { stdio: "inherit", windowsHide: true });
    fs.renameSync(archive, path.join(output, "video-runtime.7z"));
    fs.copyFileSync(path.join(source, "manifest.json"), path.join(output, "manifest.json"));
  } finally {
    fs.rmSync(archive, { force: true });
    fs.rmdirSync(temporary);
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
