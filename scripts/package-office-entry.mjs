import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import * as asar from '@electron/asar';
import electronFuses from '@electron/fuses';

// Explicitly scoped resource update. It never launches/kills an app, contacts a
// server, reads a personal profile, or updates any resource other than app.asar.
// Default: prepare. Optional: --with-runtime copies a menu-test-only EXE mirror.
// install: requires a freshly verified prepared manifest and a stopped target.
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TARGET = 'D:/maiyi/signaldesk-desktop';
const INSTALLED = path.resolve(TARGET, 'resources/app.asar');
const OUTPUT = path.join(ROOT, 'release/internal-management-entry-20261103');
const EXPECTED_MAIN = 'a1d946169d81df58ab8bd21f75c566a8664633ff73faa9bbf37e6286df43b4d6';
const OFFICE_URL = 'https://113.46.159.100/travel/';
const MODE = process.argv[2] || 'prepare';
const WITH_RUNTIME = process.argv.includes('--with-runtime');
const {getCurrentFuseWire, FuseV1Options} = electronFuses;
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const json = value => JSON.stringify(value, null, 2) + '\n';
const stamp = () => new Date().toISOString().replace(/\D/g, '').slice(0, 17) + '-' + crypto.randomBytes(4).toString('hex');
const normalize = value => process.platform === 'win32' ? value.toLowerCase() : value;
const equalPath = (a, b) => normalize(path.resolve(a)) === normalize(path.resolve(b));
// ASAR's Windows filesystem walker expects native separators at lookup time;
// manifest/member names remain POSIX for portable comparison.
const archiveFile = (archive, member) => asar.extractFile(archive, member.replaceAll('/', path.sep));

function inside(root, value) {
  assert.equal(typeof value, 'string');
  assert(value && !value.includes('\\') && !value.includes('\0') && !path.posix.isAbsolute(value));
  assert(!value.split('/').some(part => !part || part === '.' || part === '..' || part.includes(':')));
  const target = path.resolve(root, ...value.split('/'));
  const relative = path.relative(root, target);
  assert(relative && !relative.startsWith('..') && !path.isAbsolute(relative), 'Path escaped its allowed root');
  return target;
}

async function plain(target, {missing = false} = {}) {
  target = path.resolve(target);
  for (let current = target;; current = path.dirname(current)) {
    try {
      const info = await fs.lstat(current);
      assert(!info.isSymbolicLink(), 'Directory links are not allowed: ' + current);
      assert(equalPath(await fs.realpath(current), current), 'Resolved path escaped through a reparse point');
    } catch (error) {
      if (!missing || error.code !== 'ENOENT') throw error;
    }
    if (path.dirname(current) === current) break;
  }
}

async function bytes(file) {
  await plain(file);
  const info = await fs.lstat(file);
  assert(info.isFile(), 'Expected a regular file: ' + file);
  return fs.readFile(file);
}

async function writeJson(file, value, exclusive = false) {
  await plain(file, {missing: true});
  await fs.mkdir(path.dirname(file), {recursive: true});
  await fs.writeFile(file, json(value), exclusive ? {flag: 'wx'} : undefined);
}

async function fileRows(directory, relative = '') {
  await plain(relative ? inside(directory, relative) : directory);
  const rows = [];
  for (const item of await fs.readdir(relative ? inside(directory, relative) : directory, {withFileTypes: true})) {
    const name = relative ? relative + '/' + item.name : item.name;
    assert(!item.isSymbolicLink(), 'Source links are not allowed');
    if (item.isDirectory()) rows.push(...await fileRows(directory, name));
    else {
      assert(item.isFile());
      const data = await bytes(inside(directory, name));
      rows.push({path: name, bytes: data.length, sha256: sha(data)});
    }
  }
  return rows.sort((a, b) => a.path.localeCompare(b.path));
}

function archiveRows(archive) {
  asar.uncache(archive);
  const header = asar.getRawHeader(archive).header;
  assert(header?.files && typeof header.files === 'object');
  const rows = [], seen = new Set();
  function walk(files, prefix = '') {
    for (const [name, record] of Object.entries(files)) {
      assert(name && !/[\\/:\0]/.test(name) && name !== '.' && name !== '..', 'Unsafe ASAR member');
      const member = prefix ? prefix + '/' + name : name;
      assert(!seen.has(member.toLowerCase()), 'ASAR has case-colliding members');
      seen.add(member.toLowerCase());
      // This installed R2 archive has none of these flags. Refuse rather than
      // silently repack future unpacked/native/link/executable layouts wrongly.
      assert(!record.link && !record.unpacked && !record.executable,
        'Unsupported ASAR link, unpacked or executable layout; installed archive was preserved');
      if (record.files) {
        rows.push({path: member, type: 'directory', unpacked: false, executable: false});
        walk(record.files, member);
      } else {
        assert(Number.isInteger(record.size) && record.size >= 0);
        const data = archiveFile(archive, member);
        assert.equal(data.length, record.size);
        rows.push({path: member, type: 'file', unpacked: false, executable: false,
          bytes: data.length, sha256: sha(data)});
      }
    }
  }
  walk(header.files);
  return rows.sort((a, b) => a.path.localeCompare(b.path));
}

const isDist = row => row.path === 'dist' || row.path.startsWith('dist/');
const unchangedRows = rows => rows.filter(row => !isDist(row));

async function assertInstalledSourceIdentity(archive) {
  const pkg = JSON.parse(archiveFile(archive, 'package.json'));
  assert.equal(pkg.name, 'signaldesk-desktop'); assert.equal(pkg.version, '0.6.4');
  assert.equal(pkg.main, 'electron/main.cjs');
  assert.equal(sha(archiveFile(archive, 'electron/main.cjs')), EXPECTED_MAIN,
    'Installed main process is outside the verified R2 baseline');
  const sourcePkg = JSON.parse(await bytes(path.join(ROOT, 'package.json')));
  assert.equal(sourcePkg.name, pkg.name); assert.equal(sourcePkg.version, pkg.version);
  for (const row of archiveRows(archive).filter(row => row.type === 'file' && row.path.startsWith('electron/')))
    assert.equal(sha(await bytes(inside(ROOT, row.path))), row.sha256,
      'Source Electron module differs from the preserved installed R2 module: ' + row.path);
}

async function sourceProof() {
  const rows = [];
  for (const name of ['src', 'electron'])
    rows.push(...(await fileRows(path.join(ROOT, name))).map(row => ({...row, path: name + '/' + row.path})));
  for (const name of ['package.json', 'package-lock.json', 'index.html', 'vite.config.ts', 'tsconfig.json']) {
    const data = await bytes(path.join(ROOT, name));
    rows.push({path: name, bytes: data.length, sha256: sha(data)});
  }
  rows.sort((a, b) => a.path.localeCompare(b.path));
  return {sha256: sha(json(rows)), files: rows};
}

async function runtimeProof() {
  const executable = path.join(TARGET, '迈医协同.exe');
  const data = await bytes(executable);
  const fuses = await getCurrentFuseWire(executable);
  // Read only. Never turn off integrity validation to make a patch work.
  assert.equal(fuses.version, '1', 'Unknown Electron fuse layout');
  assert.equal(fuses[FuseV1Options.EnableEmbeddedAsarIntegrityValidation], 48,
    'Embedded ASAR validation is enabled or unknown; a complete reviewed installer is required');
  return {executable, sha256: sha(data), bytes: data.length, fuseVersion: fuses.version,
    embeddedAsarIntegrityFuse: fuses[FuseV1Options.EnableEmbeddedAsarIntegrityValidation]};
}

async function distProof() {
  const directory = path.join(ROOT, 'dist');
  const rows = await fileRows(directory);
  assert(rows.length >= 4, 'Compiled dist is missing');
  assert(rows.every(row => !/(?:^|\/)(?:\.env(?:\.|$)|.*\.(?:db|sqlite3?|key|pem)$)/i.test(row.path)));
  const html = (await bytes(path.join(directory, 'index.html'))).toString('utf8');
  const refs = [...html.matchAll(/\b(?:src|href)=["']([^"']+)["']/g)].map(match => match[1]);
  assert(refs.length >= 2, 'Compiled index has no script and stylesheet');
  for (const reference of refs) {
    assert(!/^(?:[a-z][a-z0-9+.-]*:|\/|#)/i.test(reference), 'Desktop entry assets must be relative');
    assert(!reference.includes('?') && !reference.includes('#'));
    const local = reference.replace(/^\.\//, '');
    assert(rows.some(row => row.path === local), 'Compiled index references a missing asset');
  }
  const javascript = (await Promise.all(rows.filter(row => row.path.endsWith('.js'))
    .map(row => bytes(inside(directory, row.path))))).map(value => value.toString('utf8')).join('\n');
  const decoded = javascript.replace(/\\u([0-9a-f]{4})/gi, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
  assert(decoded.includes(OFFICE_URL) && decoded.includes('内部管理系统'), 'Compiled menu entry is missing');
  return {sha256: sha(json(rows)), files: rows, indexReferences: refs};
}

async function copyExclusive(source, destination, expectedHash) {
  await plain(destination, {missing: true});
  await fs.mkdir(path.dirname(destination), {recursive: true});
  if (expectedHash) assert.equal(sha(await bytes(source)), expectedHash);
  await fs.copyFile(source, destination, 1 /* COPYFILE_EXCL */);
  assert.equal(sha(await bytes(destination)), expectedHash || sha(await bytes(source)));
}

async function candidateProof(manifest) {
  assert.equal(manifest.kind, 'maiyi-office-entry-resource-update');
  assert.equal(manifest.targetArchive, INSTALLED);
  assert.equal(manifest.officeUrl, OFFICE_URL);
  const candidateDir = inside(OUTPUT, manifest.candidateDirectory);
  const candidate = path.join(candidateDir, 'app.asar');
  assert.equal(sha(await bytes(candidate)), manifest.candidateSha256, 'Candidate archive drifted');
  const rows = archiveRows(candidate);
  assert.deepEqual(rows, manifest.members, 'Candidate ASAR member bytes or flags drifted');
  assert.deepEqual(unchangedRows(rows), manifest.preservedMembers, 'Candidate changed a non-dist member');
  assert.equal(sha(archiveFile(candidate, 'electron/main.cjs')), EXPECTED_MAIN);
  assert.deepEqual(await sourceProof(), manifest.source, 'Client source changed after package preparation');
  assert.deepEqual(await runtimeProof(), manifest.runtime, 'Installed runtime changed after package preparation');
  assert.deepEqual(await distProof(), manifest.dist, 'Compiled dist changed after package preparation');
  for (const row of manifest.dist.files) {
    const actual = rows.find(value => value.path === 'dist/' + row.path);
    assert(actual && actual.type === 'file' && actual.sha256 === row.sha256 && actual.bytes === row.bytes);
  }
  assert.equal(rows.filter(row => row.type === 'file' && isDist(row)).length, manifest.dist.files.length);
  return {candidateDir, candidate, rows};
}

async function copyRuntimeMirror(candidateDir, candidate) {
  // Menu-test mirror only: no personal data, updater state, logs, or video backend.
  // The actual installation keeps all its existing resources, including video.
  const mirror = path.join(candidateDir, 'win-unpacked');
  await plain(mirror, {missing: true}); await fs.mkdir(mirror);
  const runtime = [];
  for (const item of await fs.readdir(TARGET, {withFileTypes: true})) {
    if (!item.isFile() || !(item.name === '迈医协同.exe' || /\.(?:dll|pak|bin|dat)$/.test(item.name) || item.name === 'vk_swiftshader_icd.json')) continue;
    const data = await bytes(path.join(TARGET, item.name));
    await copyExclusive(path.join(TARGET, item.name), path.join(mirror, item.name), sha(data));
    runtime.push({path: item.name, sha256: sha(data), bytes: data.length});
  }
  assert(runtime.some(row => row.path === '迈医协同.exe'));
  for (const row of await fileRows(path.join(TARGET, 'locales'))) {
    assert(row.path.endsWith('.pak'));
    await copyExclusive(inside(path.join(TARGET, 'locales'), row.path), inside(path.join(mirror, 'locales'), row.path), row.sha256);
    runtime.push({...row, path: 'locales/' + row.path});
  }
  await copyExclusive(candidate, path.join(mirror, 'resources/app.asar'));
  return {directory: mirror, files: runtime, menuTestOnly: true, videoRuntimeCopied: false,
    note: 'This isolated menu-test mirror is not a complete installer; the live update replaces only app.asar.'};
}

async function prepare() {
  await plain(INSTALLED); await plain(OUTPUT, {missing: true});
  await fs.mkdir(OUTPUT, {recursive: true});
  const original = await bytes(INSTALLED), originalSha = sha(original);
  await assertInstalledSourceIdentity(INSTALLED);
  const baseline = archiveRows(INSTALLED), source = await sourceProof(), dist = await distProof();
  const runtime = await runtimeProof();
  const name = 'candidate-' + stamp();
  const directory = inside(OUTPUT, name), tree = path.join(directory, 'asar-tree');
  await fs.mkdir(directory); await fs.mkdir(tree);
  assert.equal(sha(await bytes(INSTALLED)), originalSha, 'Installed app changed during preparation');
  asar.extractAll(INSTALLED, tree);
  // The header was already rejected if it contained any link, and tree was an
  // exclusively created directory. Verify each extracted leaf without repeating
  // every ancestor realpath ~4700 times (the ordinary input paths still use the
  // full plain-path guard). No existing output is used as the extraction tree.
  for (const row of baseline.filter(row => row.type === 'file')) {
    const extracted = inside(tree, row.path), info = await fs.lstat(extracted);
    assert(info.isFile() && !info.isSymbolicLink(), 'Extracted member is not a regular file');
    assert.equal(sha(await fs.readFile(extracted)), row.sha256, 'ASAR extraction differed');
  }
  const oldDist = path.join(tree, 'dist');
  await plain(oldDist);
  assert(equalPath(oldDist, path.join(directory, 'asar-tree/dist')) && path.relative(directory, oldDist) === path.join('asar-tree', 'dist'));
  // Only this fresh, verified candidate's extracted dist is removed. Old program
  // files, original outputs, archives, and previous candidates are never deleted.
  await fs.rm(oldDist, {recursive: true});
  await fs.mkdir(oldDist);
  for (const row of dist.files)
    await copyExclusive(inside(path.join(ROOT, 'dist'), row.path), inside(oldDist, row.path), row.sha256);
  const candidate = path.join(directory, 'app.asar');
  await asar.createPackageWithOptions(tree, candidate, {dot: true});
  const members = archiveRows(candidate);
  assert.deepEqual(unchangedRows(members), unchangedRows(baseline), 'Packing changed a non-dist member');
  assert.equal(sha(await bytes(INSTALLED)), originalSha, 'Installed app changed during packing');
  assert.deepEqual(await sourceProof(), source);
  assert.deepEqual(await distProof(), dist);
  const manifest = {kind: 'maiyi-office-entry-resource-update', schema: 1, at: new Date().toISOString(),
    status: 'PREPARED_NOT_INSTALLED', targetArchive: INSTALLED, candidateDirectory: name,
    installedSha256: originalSha, candidateSha256: sha(await bytes(candidate)), officeUrl: OFFICE_URL,
    source, dist, runtime, preservedMembers: unchangedRows(baseline), members,
    originalMainSha256: EXPECTED_MAIN, version: '0.6.4', profileAccessed: false,
    serverDeployment: false, osBrowserTested: false, noProcessKilled: true};
  await candidateProof(manifest);
  if (WITH_RUNTIME) manifest.runtimeMirror = await copyRuntimeMirror(directory, candidate);
  await writeJson(path.join(directory, 'manifest.json'), manifest, true);
  const pointer = {candidateDirectory: name, manifestSha256: sha(await bytes(path.join(directory, 'manifest.json')))};
  const pendingPointer = path.join(OUTPUT, '.current-' + stamp() + '.json');
  await writeJson(pendingPointer, pointer, true);
  await fs.rename(pendingPointer, path.join(OUTPUT, 'current-candidate.json'));
  console.log(JSON.stringify({status: 'PASS', mode: 'prepare', directory, candidate,
    manifest: path.join(directory, 'manifest.json'), installedSha256: originalSha,
    candidateSha256: manifest.candidateSha256, preservedNonDistMembers: manifest.preservedMembers.length,
    distFiles: dist.files.length, runtimeMirror: manifest.runtimeMirror?.directory || null,
    installed: false, noProcessKilled: true, profileAccessed: false}));
}

async function loadCandidate() {
  await plain(OUTPUT);
  const pointer = JSON.parse(await bytes(path.join(OUTPUT, 'current-candidate.json')));
  assert(/^candidate-[0-9]{17}-[a-f0-9]{8}$/.test(pointer.candidateDirectory));
  const file = path.join(inside(OUTPUT, pointer.candidateDirectory), 'manifest.json');
  const data = await bytes(file); assert.equal(sha(data), pointer.manifestSha256);
  return JSON.parse(data);
}

function assertStopped() {
  assert.equal(process.platform, 'win32', 'Live installation is supported only on Windows');
  const query = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); $rows=@(Get-CimInstance Win32_Process -Filter \"Name='迈医协同.exe'\" | Select-Object ProcessId,ExecutablePath); ConvertTo-Json -InputObject $rows -Compress";
  const result = spawnSync('powershell.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', query],
    {encoding: 'utf8', windowsHide: true, timeout: 30000});
  assert(!result.error && result.status === 0, 'Unable to confirm that installed application is stopped; nothing was killed');
  const rows = JSON.parse(result.stdout.trim().replace(/^\uFEFF/, '') || '[]');
  assert(Array.isArray(rows), 'Process inventory was not an array');
  for (const row of rows) {
    assert(row.ExecutablePath, 'A matching application process could not be identified; close it manually');
    assert(!equalPath(row.ExecutablePath, path.join(TARGET, '迈医协同.exe')) &&
      !equalPath(path.dirname(row.ExecutablePath), TARGET),
      'Close the installed 迈医协同 application before installing; no process was killed');
  }
}

async function install() {
  const manifest = await loadCandidate(), prepared = await candidateProof(manifest);
  await plain(INSTALLED); await plain(OUTPUT); assertStopped();
  assert.equal(sha(await bytes(INSTALLED)), manifest.installedSha256,
    'Installed archive differs from the prepared baseline; installation refused');
  const lockPath = path.join(TARGET, 'resources/.maiyi-resource-update.lock');
  await plain(lockPath, {missing: true});
  const token = crypto.randomUUID(), lock = await fs.open(lockPath, 'wx');
  await lock.writeFile(json({kind: 'office-entry-resource-update', token, pid: process.pid, at: new Date().toISOString()}));
  let backup, pending, switched = false;
  try {
    assertStopped();
    assert.equal(sha(await bytes(INSTALLED)), manifest.installedSha256);
    const backupDirectory = path.join(OUTPUT, 'backups', stamp());
    await plain(backupDirectory, {missing: true}); await fs.mkdir(backupDirectory, {recursive: true});
    backup = path.join(backupDirectory, 'app.asar');
    await copyExclusive(INSTALLED, backup, manifest.installedSha256);
    await writeJson(path.join(backupDirectory, 'backup.json'), {kind: 'office-entry-original-asar',
      targetArchive: INSTALLED, sourceSha256: manifest.installedSha256,
      candidateSha256: manifest.candidateSha256, officeUrl: OFFICE_URL, at: new Date().toISOString()}, true);
    pending = path.join(TARGET, 'resources/app.asar.office-pending-' + token);
    await copyExclusive(prepared.candidate, pending, manifest.candidateSha256);
    assertStopped();
    assert.equal(sha(await bytes(INSTALLED)), manifest.installedSha256);
    assert.deepEqual(await sourceProof(), manifest.source);
    // Same-directory rename atomically replaces only app.asar; backup remains.
    await fs.rename(pending, INSTALLED); switched = true;
    assert.equal(sha(await bytes(INSTALLED)), manifest.candidateSha256);
    assert.deepEqual(archiveRows(INSTALLED), manifest.members);
    const result = {status: 'PASS', mode: 'install', at: new Date().toISOString(),
      targetArchive: INSTALLED, beforeSha256: manifest.installedSha256,
      afterSha256: manifest.candidateSha256, backup, candidateDirectory: manifest.candidateDirectory,
      preservedNonDistMembers: manifest.preservedMembers.length, mainSha256: EXPECTED_MAIN,
      updatedFiles: ['resources/app.asar'], exeUpdated: false, videoRuntimeUpdated: false,
      profileAccessed: false, serverDeployment: false, noProcessKilled: true,
      applicationStarted: false, nativeRuntimeVerified: false};
    await writeJson(path.join(prepared.candidateDir, 'install.json'), result, true);
    await writeJson(path.join(OUTPUT, 'last-install.json'), result);
    console.log(JSON.stringify(result));
  } catch (error) {
    let restored = false;
    if (switched && backup) {
      // Restore only when the live program has not been launched. Never kill it
      // or replace resources beneath a running user's process to force rollback.
      try {
        assertStopped();
        const current = sha(await bytes(INSTALLED));
        assert.equal(current, manifest.candidateSha256, 'Live archive drifted after switch; manual recovery required');
        const restore = path.join(TARGET, 'resources/app.asar.office-restore-' + token);
        await copyExclusive(backup, restore, manifest.installedSha256);
        await fs.rename(restore, INSTALLED);
        assert.equal(sha(await bytes(INSTALLED)), manifest.installedSha256); restored = true;
      } catch { /* Preserve backup and evidence; report incomplete rollback. */ }
    }
    await writeJson(path.join(OUTPUT, 'install-failure-' + stamp() + '.json'), {
      status: 'FAIL', mode: 'install', at: new Date().toISOString(), error: error.message,
      switched, restored, backup: backup || null, pending: pending || null,
      noProcessKilled: true, profileAccessed: false});
    throw new Error(`Resource installation failed; switched=${switched}, restored=${restored}; ${error.message}`);
  } finally {
    await lock.close();
    // Delete only this process's known single lock file; never a directory.
    try {
      const current = JSON.parse(await bytes(lockPath));
      if (current.token === token) await fs.unlink(lockPath);
    } catch { /* Do not remove a lock that another owner changed. */ }
  }
}

async function verify() {
  const manifest = await loadCandidate(); await candidateProof(manifest);
  assert.equal(sha(await bytes(INSTALLED)), manifest.candidateSha256);
  assert.deepEqual(archiveRows(INSTALLED), manifest.members);
  const result = {status: 'PASS', mode: 'verify', at: new Date().toISOString(),
    targetArchive: INSTALLED, sha256: manifest.candidateSha256,
    preservedNonDistMembers: manifest.preservedMembers.length, mainSha256: EXPECTED_MAIN,
    nativeRuntimeVerified: false, profileAccessed: false, noProcessKilled: true};
  await writeJson(path.join(OUTPUT, 'installed-verification.json'), result);
  console.log(JSON.stringify(result));
}

assert(['prepare', 'install', 'verify'].includes(MODE), 'Use prepare [--with-runtime], install or verify');
process.chdir(ROOT);
try {
  if (MODE === 'prepare') await prepare();
  else if (MODE === 'install') await install();
  else await verify();
} catch (error) {
  console.error(JSON.stringify({status: 'FAIL', mode: MODE, error: error.message,
    noProcessKilled: true, profileAccessed: false}));
  process.exitCode = 1;
}
