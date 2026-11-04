// Real Edge + real local API. Synthetic page/media and pre-granted test hosts.
// The browser permission prompt and actual activeTab gesture remain manual checks.
import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createServer } from 'node:net';
import { createHash } from 'node:crypto';
import { cp, mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
const output = process.argv[2];
if (!output) throw new Error('Evidence directory required');
await mkdir(output, { recursive: true });
const run = await mkdtemp(path.join(output, 'run-'));
const portServer = createServer(); await new Promise(resolve => portServer.listen(0, '127.0.0.1', resolve));
const port = portServer.address().port; await new Promise(resolve => portServer.close(resolve));
const base = `http://127.0.0.1:${port}`;
const backend = spawn(path.resolve('../.venv-signaldesk/Scripts/python.exe'), ['server/tests/knowledge_preview.py', String(port)], {
  windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1',
    TASK_DATABASE_URL: `sqlite:///${path.join(run, 'knowledge-e2e.db').replaceAll('\\', '/')}`,
    TASK_API_KEY: 'synthetic-extension-test', TASK_KNOWLEDGE_ASR_ENABLED: 'false',
    TASK_KNOWLEDGE_STORAGE_DIR: path.join(run, 'files'), TASK_PROMPT_API_KEY: '',
    TASK_KNOWLEDGE_GATEWAY_SECRET: '', TASK_HERMES_WEBHOOK_URL: '', TASK_KNOWLEDGE_CLOUD_QA_ENABLED: 'false' },
});
let diagnostic = ''; backend.stderr.on('data', value => { diagnostic += value; });
const checked = [], outgoing = []; let context;
try {
  await expect.poll(async () => { try { return (await fetch(base + '/health')).status; } catch { return 0; } }, { timeout: 20000 }).toBe(200);
  const extension = path.join(run, 'extension'); await cp('browser-extension', extension, { recursive: true });
  const manifest = JSON.parse(await readFile(path.join(extension, 'manifest.json'), 'utf8'));
  manifest.host_permissions = [`${base}/*`, 'https://mp.weixin.qq.com/*', 'https://synthetic-video.qq.com/*'];
  await writeFile(path.join(extension, 'manifest.json'), JSON.stringify(manifest));
  const id = createHash('sha256').update(extension, 'utf16le').digest('hex').slice(0, 32).replace(/[0-9a-f]/g, c => String.fromCharCode(97 + parseInt(c, 16)));
  const mp4Path = path.join(run, 'synthetic.mp4');
  execFileSync(path.resolve('artifacts/bundled-video-runtime/python/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'),
    ['-v', 'error', '-f', 'lavfi', '-i', 'color=c=white:s=320x180:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4Path], { windowsHide: true });
  const mp4 = await readFile(mp4Path);
  context = await chromium.launchPersistentContext(path.join(run, 'profile'), {
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true,
    ignoreDefaultArgs: ['--disable-extensions'],
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`, '--no-first-run'],
  });
  await context.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith(base + '/') || !/^https?:/.test(url)) return route.continue();
    if (url.startsWith('https://synthetic-video.qq.com/')) return route.fulfill({ status: 200, contentType: 'video/mp4', body: mp4 });
    if (url.startsWith('https://mp.weixin.qq.com/')) return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: '<h1 id="activity-name">扩展合成验收文章</h1><p id="js_name">软件测试发布者</p><div id="js_content">这是独立浏览器中的合成正文，保留原始来源和个人引用。</div><video controls width="320" src="https://synthetic-video.qq.com/test.mp4"></video>' });
    outgoing.push(new URL(url).origin); return route.abort();
  });
  const article = await context.newPage(); await article.goto('https://mp.weixin.qq.com/s/synthetic-extension-only');
  await expect.poll(() => article.locator('video').evaluate(video => video.readyState), { timeout: 10000 }).toBeGreaterThanOrEqual(1);
  const popup = await context.newPage(); await popup.goto(`chrome-extension://${id}/popup.html`);
  await popup.getByLabel('你的后台地址').fill(base);
  await popup.getByLabel('个人账号', { exact: true }).fill('preview-kb');
  await popup.getByLabel('密码', { exact: true }).fill('preview-only-password');
  await popup.getByRole('button', { name: '登录本人后台' }).click();
  await expect(popup.getByRole('status')).toContainText('已登录', { timeout: 12000 });
  expect(await popup.getByLabel('密码', { exact: true }).inputValue()).toBe('');
  checked.push('real optional-permission request accepts explicit local origin; real API login; password cleared');
  await popup.evaluate(async url => {
    const [tab] = await chrome.tabs.query({ url });
    if (!tab) throw new Error('Synthetic article tab missing');
    window.syntheticTab = tab;
    chrome.tabs.query = async () => [window.syntheticTab];
  }, article.url());
  await popup.getByRole('button', { name: '预览当前微信页面' }).click();
  await expect(popup.getByLabel('可见正文（可修订）')).toHaveValue(/合成正文/);
  await expect(popup.getByRole('button', { name: '保存正文 / 链接' })).toBeDisabled();
  await popup.getByLabel('我有权保存，确认发送以上正文与来源到本人后台').check();
  await popup.getByLabel('标题', { exact: true }).fill('扩展合成验收文章修订');
  await expect(popup.getByLabel('我有权保存，确认发送以上正文与来源到本人后台')).not.toBeChecked();
  await popup.getByLabel('我有权保存，确认发送以上正文与来源到本人后台').check();
  await popup.getByRole('button', { name: '保存正文 / 链接' }).click();
  await expect(popup.getByRole('status')).toContainText('资料编号');
  const saved = await popup.getByRole('status').innerText();
  checked.push('real scripting extraction of visible synthetic WeChat DOM, consent reset on edit and persisted source');
  await popup.getByLabel('我有权保存，确认发送以上正文与来源到本人后台').check();
  await popup.getByRole('button', { name: '保存正文 / 链接' }).click();
  await expect(popup.getByRole('status')).toHaveText(saved);
  checked.push('same-link deduplication preserves the saved original');
  await popup.getByLabel('我有权保存当前视频，确认上传原件到本人后台（最多 25 MB）').check();
  await popup.getByRole('button', { name: '获取可访问的视频原件并保存' }).click();
  await expect(popup.getByRole('status')).toContainText('原件已保存', { timeout: 12000 });
  await popup.screenshot({ path: path.join(output, 'extension-saved.png'), fullPage: true });
  checked.push('real streamed synthetic MP4, SHA-256/idempotency headers and real file capture API');
  await popup.evaluate(() => { window.syntheticTab = { ...window.syntheticTab, url: 'https://example.test/rejected' }; });
  await popup.getByRole('button', { name: '预览当前微信页面' }).click();
  await expect(popup.getByRole('status')).toContainText('请打开 HTTPS 公众号');
  await popup.getByRole('button', { name: '退出账号' }).click();
  await expect(popup.getByRole('button', { name: '登录本人后台' })).toBeVisible();
  expect(await popup.evaluate(async () => (await chrome.storage.session.get('hermesSession')).hermesSession)).toBeUndefined();
  checked.push('unsupported tab rejected; logout clears session');
  const result = { passed: true, browser: await context.browser().version(), checked, outside_requests_blocked: outgoing,
    synthetic_page_and_media: true, real_local_api: true, test_adapters: ['pre-granted only synthetic hosts', 'active tab selector points to synthetic page'],
    not_verified: ['native optional-permission confirmation UI', 'toolbar activeTab user gesture', 'actual WeChat live page/video access'], paid_service_calls: false };
  await writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2)); console.log(JSON.stringify(result));
} catch (error) {
  await writeFile(path.join(output, 'failure.txt'), String(error) + '\n' + diagnostic);
  throw error;
} finally { if (context) await context.close(); backend.kill(); }
