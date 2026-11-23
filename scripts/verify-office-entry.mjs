import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(root);
const docs = 'docs/delivery/internal-management-entry-20261103';
const release = 'release/internal-management-entry-20261103';
const read = file => JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
const sha = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const pointer = read(`${release}/current-candidate.json`);
assert(/^candidate-[0-9]{17}-[a-f0-9]{8}$/.test(pointer.candidateDirectory));
const manifestPath = `${release}/${pointer.candidateDirectory}/manifest.json`;
assert.equal(sha(manifestPath), pointer.manifestSha256);
const manifest = read(manifestPath), installed = read(`${release}/last-install.json`);
const verified = read(`${release}/installed-verification.json`);
for (const result of [installed, verified]) assert.equal(result.status, 'PASS');
assert.equal(installed.afterSha256, manifest.candidateSha256);
assert.equal(verified.sha256, manifest.candidateSha256);
assert.equal(sha('D:/maiyi/signaldesk-desktop/resources/app.asar'), manifest.candidateSha256);
assert.equal(sha(installed.backup), manifest.installedSha256);
assert.equal(installed.exeUpdated, false); assert.equal(installed.videoRuntimeUpdated, false);
assert.equal(installed.profileAccessed, false); assert.equal(installed.serverDeployment, false);
assert.equal(installed.noProcessKilled, true);
assert.equal(sha('D:/maiyi/signaldesk-desktop/迈医协同.exe'), manifest.runtime.sha256);
for (const file of manifest.source.files) assert.equal(sha(file.path), file.sha256);
for (const file of manifest.dist.files) assert.equal(sha(`dist/${file.path}`), file.sha256);
assert.equal(verified.mainSha256, sha('electron/main.cjs'));
assert.equal(manifest.officeUrl, 'https://113.46.159.100/travel/');
const build = fs.readFileSync(`${docs}/evidence/build.log`, 'utf8');
assert(/Test Files\s+25 passed \(25\)/.test(build));
assert(/Tests\s+200 passed \(200\)/.test(build)); assert(/built in/.test(build));
const candidate = read(`${docs}/evidence/native-candidate/run-RJIgCJ/results.json`);
const native = read(`${docs}/evidence/native-installed/run-8GDyx9/results.json`);
for (const result of [candidate, native]) {
  assert.equal(result.status, 'PASS'); assert.equal(result.counts.pass, 7); assert.equal(result.counts.fail, 0);
  assert.equal(result.sourceBinding.archiveSha256, manifest.candidateSha256);
  assert.equal(result.ownedApplicationClosed, true); assert.equal(result.ownedProcessExit.exitCode, 0);
  assert.equal(result.nativeCounters.notifications, 0); assert.equal(result.nativeCounters.updateInstalls, 0);
  assert.equal(result.nativeCounters.blockedRequests, 0); assert.equal(result.pageErrors.length, 0);
  for (const screenshot of result.screenshots) assert.equal(sha(screenshot.path), screenshot.sha256);
}
assert.equal(native.realOsBrowserOpenRequestAccepted, true);
assert.equal(native.realOsBrowserPageObserved, false);
assert.equal(native.nativeCounters.realBrowserOpenRequests, 1);
assert.equal(native.nativeCounters.realBrowserOpenAccepted, 1);
assert.equal(native.nativeCounters.realBrowserOpenFailed, 0);
const target = read(`${docs}/evidence/target/results.json`);
assert.equal(target.status, 'PASS'); assert.equal(target.tlsValidationEnabled, true);
assert.equal(target.authenticated, false); assert.equal(target.businessWrites, 0);
const baseline = read(`${docs}/evidence/baseline-renderer-comparison.json`);
assert.equal(baseline.status, 'PASS'); assert.equal(baseline.files, 9);
const format = read(`${docs}/evidence/source-backup-format.json`);
assert.equal(sha(format.backup), format.sha256);
const artifactPaths = ['README.md', 'src/components/Sidebar.tsx', 'src/styles.css',
  `${docs}/delivery.md`, `${docs}/test-report.md`, `${docs}/packaging.md`,
  'scripts/package-office-entry.mjs', 'scripts/office-entry-e2e.mjs'];
const result = {at: new Date().toISOString(), status: 'PASS', installedLocally: true,
  sourceRoot: root, clientVersion: manifest.version, archiveSha256: manifest.candidateSha256,
  program: 'D:/maiyi/signaldesk-desktop/迈医协同.exe', officeUrl: manifest.officeUrl,
  tests: {sourceFiles: 25, sourceTests: 200, candidateNative: 7, installedNative: 7, targetPublicLogin: 2},
  realOsBrowserOpenRequestAccepted: true, realOsBrowserPageObserved: false,
  preservedNonDistMembers: installed.preservedNonDistMembers, backup: installed.backup,
  realCollaborationProfileAccessed: false, serverDeployed: false,
  artifacts: Object.fromEntries(artifactPaths.map(file => [file, sha(file)]))};
fs.writeFileSync(`${docs}/evidence/final.json`, JSON.stringify(result, null, 2));
console.log(JSON.stringify(result));
