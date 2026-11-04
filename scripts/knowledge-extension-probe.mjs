// Isolated browser profile. No access to the user's browser or accounts.
import { chromium } from '@playwright/test';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
const output = process.argv[2];
if (!output) throw new Error('Evidence directory required');
await mkdir(output, { recursive: true });
const profile = await mkdtemp(path.join(output, 'profile-'));
const extension = path.resolve('browser-extension');
const context = await chromium.launchPersistentContext(profile, {
  executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true,
  ignoreDefaultArgs: ['--disable-extensions'],
  args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`, '--no-first-run'],
});
try {
  const page = await context.newPage(); await page.goto('edge://extensions/');
  const extensionId = createHash('sha256').update(extension, 'utf16le').digest('hex').slice(0, 32).replace(/[0-9a-f]/g, c => String.fromCharCode(97 + parseInt(c, 16)));
  if (!extensionId) throw new Error('Extension ID missing after details navigation');
  await page.goto(`chrome-extension://${extensionId}/popup.html`);
  await page.getByLabel('你的后台地址').fill('http://127.0.0.1:18765');
  await page.getByLabel('个人账号', { exact: true }).fill('synthetic-probe');
  await page.getByLabel('密码', { exact: true }).fill('synthetic-no-account');
  await page.getByRole('button', { name: '登录本人后台' }).click();
  await page.waitForTimeout(2000);
  const data = await page.evaluate(() => {
    const collect = root => [...root.querySelectorAll('*')].flatMap(el => [
      ...(el.tagName.toLowerCase().includes('extension') ? [{ tag: el.tagName, id: el.id, text: el.textContent.slice(0, 800) }] : []),
      ...(el.shadowRoot ? collect(el.shadowRoot) : []),
    ]);
    return { text: document.body.innerText.slice(0, 6000), elements: collect(document) };
  });
  data.extensionId = extensionId;
  await writeFile(path.join(output, 'probe.json'), JSON.stringify(data, null, 2));
  await page.screenshot({ path: path.join(output, 'extensions.png') });
  console.log(JSON.stringify(data));
} finally { await context.close(); }
