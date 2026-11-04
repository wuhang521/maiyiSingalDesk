/** Build resources for the existing 迈医协同 installation; never copy local data. */
import { createHash, randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputRoot = "D:/maiyi/outputs/maiyi-video-integrated-20261022";
const defaultPython = "D:/maiyi/SignalDesk-源码交接-2026-09-16/SignalDesk-源码交接/.venv-signaldesk/Scripts/python.exe";
const require = createRequire(import.meta.url);
const asar = require("@electron/asar");
const runtimeFiles = ["chrome_100_percent.pak", "chrome_200_percent.pak", "d3dcompiler_47.dll", "dxcompiler.dll", "dxil.dll", "ffmpeg.dll", "icudtl.dat", "libEGL.dll", "libGLESv2.dll", "resources.pak", "snapshot_blob.bin", "v8_context_snapshot.bin", "vk_swiftshader.dll", "vulkan-1.dll"];
const forbiddenNames = new Set(["video-config.json", "local-login.json", "trial.db", "running.json", "auth-session.json", "credentials.json", "api_key"]);
const ignoredDirectories = new Set(["__pycache__", ".pytest_cache", ".git", "node_modules"]);
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

function within(parent, child) {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  return relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

async function plain(target) {
  for (let current = path.resolve(target); ; current = path.dirname(current)) {
    try {
      if ((await fs.lstat(current)).isSymbolicLink()) throw new Error("Refusing linked package paths");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (path.dirname(current) === current) break;
  }
}

function allowed(relative) {
  const name = path.basename(relative).toLowerCase();
  if (name.startsWith(".env") || forbiddenNames.has(name) || /\.(?:dpapi|db|sqlite|sqlite3|key|pem|log)$/i.test(name)) {
    throw new Error(`Refusing runtime/private file in package: ${relative}`);
  }
}

async function tree(directory) {
  await plain(directory);
  const result = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    if (ignoredDirectories.has(entry.name) || entry.name.endsWith(".pyc") || entry.name.endsWith(".pyo")) continue;
    const target = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error("Refusing linked package member");
    if (entry.isDirectory()) result.push(...await tree(target));
    else if (entry.isFile()) result.push(target);
    else throw new Error("Refusing non-file package member");
  }
  return result.sort();
}

async function collect() {
  const result = new Map();
  const add = async (source, member) => {
    await plain(source);
    if (!(await fs.stat(source)).isFile()) throw new Error(`Missing required package input: ${member}`);
    allowed(member);
    result.set(member.replaceAll("\\", "/"), source);
  };
  for (const source of await tree(path.join(root, "dist"))) await add(source, `app/${path.relative(root, source)}`);
  await add(path.join(root, "package.json"), "app/package.json");
  for (const source of await tree(path.join(root, "electron"))) {
    if (source.endsWith(".cjs")) await add(source, `app/${path.relative(root, source)}`);
  }
  for (const name of ["main.cjs", "preload.cjs", "video-desktop.cjs"]) {
    if (!result.has(`app/electron/${name}`)) throw new Error(`Missing desktop integration module: ${name}`);
  }
  for (const source of await tree(path.join(root, "server/app"))) {
    if (source.endsWith(".py")) await add(source, `video-runtime/server/app/${path.relative(path.join(root, "server/app"), source)}`);
  }
  for (const name of ["run_video_desktop.py", "run_video_local.py"]) {
    await add(path.join(root, "server/scripts", name), `video-runtime/server/scripts/${name}`);
  }
  for (const skill of ["oldx-avatar", "video-production", "video-prompt-director"]) {
    for (const source of await tree(path.join(root, "hermes-skill", skill))) {
      await add(source, `video-runtime/hermes-skill/${skill}/${path.relative(path.join(root, "hermes-skill", skill), source)}`);
    }
  }
  if (!result.has("app/dist/index.html")) throw new Error("Build dist/index.html before packaging");
  return [...result.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function options(argv) {
  const result = { output: path.join(outputRoot, "stage"), python: defaultPython, dryRun: false, replace: false };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--dry-run") result.dryRun = true;
    else if (flag === "--replace-stage") result.replace = true;
    else if (["--output", "--python"].includes(flag) && argv[index + 1]) result[flag.slice(2)] = argv[++index];
    else throw new Error("Usage: node scripts/package-video-desktop.mjs [--dry-run] [--replace-stage] [--output path] [--python path]");
  }
  result.output = path.resolve(result.output);
  result.python = path.resolve(result.python);
  if (!within(outputRoot, result.output)) throw new Error("Stage must be a child of the dedicated output directory");
  return result;
}

async function main() {
  const args = options(process.argv.slice(2));
  await plain(args.output);
  await plain(args.python);
  if (!(await fs.stat(args.python)).isFile()) throw new Error("Dedicated Python executable is unavailable");
  const pkg = JSON.parse(await fs.readFile(path.join(root, "package.json"), "utf8"));
  if (pkg.name !== "signaldesk-desktop" || pkg.version !== "0.5.3" || pkg.main !== "electron/main.cjs" || pkg.build?.productName !== "迈医协同") throw new Error("Application identity changed; use a reviewed full installer");
  const selected = await collect();
  const electronDist = path.join(root, "node_modules/electron/dist");
  const electronVersion = (await fs.readFile(path.join(electronDist, "version"), "utf8")).trim();
  const compatibility = [];
  for (const name of runtimeFiles) compatibility.push({ path: name, sha256: sha256(await fs.readFile(path.join(electronDist, name))) });
  const summary = { dry_run: args.dryRun, stage: args.output, app_version: pkg.version, electron_version: electronVersion, input_files: selected.length, credentials_included: false, python_external: true };
  if (args.dryRun) { console.log(JSON.stringify(summary)); return; }
  let exists = false;
  try { await fs.stat(args.output); exists = true; } catch (error) { if (error.code !== "ENOENT") throw error; }
  if (exists && !args.replace) throw new Error("Stage exists; use --replace-stage to preserve it and build a replacement");
  await fs.mkdir(path.dirname(args.output), { recursive: true });
  const work = `${args.output}.building-${randomUUID()}`;
  const app = path.join(work, ".app-input");
  await fs.mkdir(app, { recursive: true });
  for (const [member, source] of selected) {
    const destination = member.startsWith("app/") ? path.join(app, member.slice(4)) : path.join(work, member);
    await fs.mkdir(path.dirname(destination), { recursive: true });
    await fs.copyFile(source, destination);
  }
  await fs.writeFile(path.join(work, "video-runtime/runtime.json"), `${JSON.stringify({ schema: 1, python: args.python.replaceAll("\\", "/") }, null, 2)}\n`);
  await asar.createPackage(app, path.join(work, "app.asar"));
  const packed = JSON.parse(asar.extractFile(path.join(work, "app.asar"), "package.json").toString("utf8"));
  if (packed.name !== pkg.name || packed.version !== pkg.version) throw new Error("Packaged application identity mismatch");
  // This is our freshly created private input directory, not an installation/data path.
  if (!within(work, app)) throw new Error("Invalid temporary application directory");
  await fs.rm(app, { recursive: true });
  const files = [];
  for (const target of await tree(work)) {
    const relative = path.relative(work, target).replaceAll("\\", "/");
    allowed(relative);
    const contents = await fs.readFile(target);
    files.push({ path: relative, bytes: contents.length, sha256: sha256(contents) });
  }
  const manifest = { schema: 1, kind: "maiyi-video-integrated-desktop", created_at: new Date().toISOString(), app_name: pkg.name, product_name: "迈医协同", app_version: pkg.version, electron_version: electronVersion, credentials_included: false, python_external: true, compatibility, files };
  await fs.writeFile(path.join(work, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  if (exists) {
    const oldManifest = JSON.parse(await fs.readFile(path.join(args.output, "manifest.json"), "utf8"));
    if (oldManifest.kind !== manifest.kind) throw new Error("Refusing to replace an unrecognized stage");
    await fs.rename(args.output, `${args.output}.previous-${Date.now()}`);
  }
  await fs.rename(work, args.output);
  console.log(JSON.stringify({ ...summary, files_verified: files.length, manifest_sha256: sha256(await fs.readFile(path.join(args.output, "manifest.json"))) }));
}

main().catch((error) => { console.error(`Desktop package failed: ${error.message}`); process.exitCode = 1; });
