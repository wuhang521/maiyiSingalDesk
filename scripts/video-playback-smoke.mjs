// Exercise the production CSP in the same Electron runtime, without model calls.
import { _electron as electron, expect } from '@playwright/test';
import { createServer } from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';

const root = process.cwd();
const require = createRequire(import.meta.url);
const evidence = path.resolve(process.env.VIDEO_PLAYBACK_EVIDENCE || 'artifacts/video-playback');
const run = path.resolve('artifacts', `video-playback-${crypto.randomUUID()}`);
await fs.mkdir(evidence, { recursive: true });
await fs.mkdir(run, { recursive: true });
const html = await fs.readFile(path.join(root, 'dist/index.html'), 'utf8');
const csp = html.match(/http-equiv="Content-Security-Policy"\s+content="([^"]+)"/i)?.[1];
if (!csp) throw new Error('Built production CSP is missing');
const oldCsp = csp.replace(/media-src\s+[^;]+;?\s*/i, '');
let wav;
if (process.env.VIDEO_PLAYBACK_AUDIO) {
  wav = await fs.readFile(process.env.VIDEO_PLAYBACK_AUDIO);
} else {
  wav = Buffer.alloc(44 + 16000 * 2 * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40);
}
if (wav.toString('ascii', 0, 4) !== 'RIFF' || wav.toString('ascii', 8, 12) !== 'WAVE') throw new Error('WAV fixture is invalid');
const playerJs = `const audio=document.querySelector('audio');
window.cspViolations=[]; document.addEventListener('securitypolicyviolation',e=>window.cspViolations.push({directive:e.effectiveDirective,blocked:e.blockedURI==='blob'||e.blockedURI.startsWith('blob:')?'blob:':e.blockedURI}));
audio.addEventListener('loadedmetadata',()=>document.getElementById('status').textContent='音频时长：'+audio.duration.toFixed(3)+' 秒');
audio.addEventListener('error',()=>document.getElementById('status').textContent='加载失败');
fetch('/audio.wav').then(r=>r.blob()).then(blob=>{audio.src=URL.createObjectURL(blob);});`;
const requests = [];
const server = createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost');
  requests.push(url.pathname);
  if (url.pathname === '/audio.wav') { response.writeHead(200, { 'Content-Type': 'audio/wav' }); response.end(wav); return; }
  if (url.pathname === '/player.js') { response.writeHead(200, { 'Content-Type': 'text/javascript' }); response.end(playerJs); return; }
  response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  response.end(`<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="${url.searchParams.has('control') ? oldCsp : csp}"><title>配音播放验证</title></head><body><h1>配音试听本地验证</h1><p id="status">加载已有音频</p><audio controls preload="metadata" aria-label="配音试听"></audio><p>仅读取已有音频，使用正式客户端内容安全策略；不调用模型。</p><script src="/player.js"></script></body></html>`);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const url = `http://127.0.0.1:${server.address().port}`;
const main = path.join(run, 'main.cjs');
await fs.writeFile(main, `const{app,BrowserWindow}=require('electron');app.setPath('userData',${JSON.stringify(path.join(run, 'profile'))});app.whenReady().then(()=>{const w=new BrowserWindow({width:1080,height:760,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});w.loadURL(${JSON.stringify(url)});});app.on('window-all-closed',()=>app.quit());`);
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => /^(PATH|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMFILES|PROGRAMFILES\(X86\)|PATHEXT|SYSTEMDRIVE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i.test(key)));
let application;
try {
  application = await electron.launch({ executablePath: require('electron'), args: [main], env });
  const page = await application.firstWindow();
  await page.route('**/*', route => route.request().url().startsWith(url) || route.request().url().startsWith('blob:') ? route.continue() : route.abort());
  await page.goto(url + '/?control=1');
  await page.waitForFunction(() => Boolean(document.querySelector('audio')?.error));
  const control = await page.locator('audio').evaluate(audio => ({ error_code: audio.error.code, error_message:audio.error.message, ready_state: audio.readyState, finite_duration: Number.isFinite(audio.duration) }));
  const violations = await page.evaluate(() => window.cspViolations);
  await fs.writeFile(path.join(evidence, 'electron-control-observed.json'), JSON.stringify({control,violations,requests,oldCsp},null,2));
  if (control.error_code !== 4 || !violations.some(item => item.directive === 'media-src' && item.blocked === 'blob:')) throw new Error('Old CSP did not reproduce the original media failure');
  await page.goto(url);
  await page.waitForFunction(() => { const a=document.querySelector('audio');return a?.readyState>=2&&Number.isFinite(a.duration)&&a.duration>0; });
  await page.locator('audio').evaluate(audio => { audio.muted=true; return audio.play(); });
  await page.waitForFunction(() => document.querySelector('audio').currentTime > .25);
  const fixed = await page.locator('audio').evaluate(audio => { audio.pause();return { duration: audio.duration, current_time: audio.currentTime, ready_state: audio.readyState, error: audio.error?.code ?? null }; });
  await expect(page.locator('#status')).toContainText('音频时长：');
  await page.screenshot({ path: path.join(evidence, 'electron-audio-playing.png') });
  const fixedViolations = await page.evaluate(() => window.cspViolations);
  if (fixedViolations.length || fixed.error !== null) throw new Error('Fixed CSP still blocked media');
  const report = { status: 'PASS', runtime: await application.evaluate(({app})=>({electron:process.versions.electron,version:app.getVersion()})), csp_source:'dist/index.html', control, control_violations:violations, fixed, fixed_violations:fixedViolations, audio_sha256:crypto.createHash('sha256').update(wav).digest('hex'), audio_bytes:wav.length, existing_audio:Boolean(process.env.VIDEO_PLAYBACK_AUDIO), paid_provider_calls:0, production_database_modified:false, fixture_requests:requests.length };
  await fs.writeFile(path.join(evidence, 'electron-playback.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  if (application) await application.close();
  await new Promise(resolve => server.close(resolve));
  // Only generated local application source/profile lives here; audio stayed in memory.
}
