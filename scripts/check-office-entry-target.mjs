import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'artifacts/task-page-access-travel-20261111/target');
await fs.mkdir(output, {recursive: true});
const target = 'https://maiyitechmed.com/travel/';
const checks = [], pageErrors = [], staticErrors = [];
let browser, failure;
try {
  const channel = process.env.OFFICE_ENTRY_BROWSER_CHANNEL;
  browser = await chromium.launch({...(channel ? {channel} : {executablePath: chromium.executablePath()}), headless: true});
  const context = await browser.newContext({ignoreHTTPSErrors: false, viewport: {width: 1440, height: 1000}});
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== new URL(target).origin || !['GET', 'HEAD'].includes(request.method()))
      return route.abort('blockedbyclient');
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.name));
  page.on('response', response => {
    if (response.url().includes('/assets/') && response.status() >= 400)
      staticErrors.push({status: response.status(), path: new URL(response.url()).pathname});
  });
  const response = await page.goto(target, {waitUntil: 'domcontentloaded', timeout: 30000});
  assert.equal(response.status(), 200);
  await expect(page).toHaveTitle('迈医内部管理系统');
  await expect(page.locator('input[type="password"]')).toBeVisible({timeout: 20000});
  checks.push({id: '目标01', status: 'PASS', description: '严格TLS下打开现有系统并挂载登录页面'});
  const scripts = await page.locator('script[src]').evaluateAll(items => items.map(item => new URL(item.src).pathname));
  assert(scripts.length > 0 && scripts.every(url => url.startsWith('/travel/')));
  assert.equal(staticErrors.length, 0); assert.equal(pageErrors.length, 0);
  checks.push({id: '目标02', status: 'PASS', description: '当前资源前缀与页面启动正常，无脚本异常'});
  await page.screenshot({path: path.join(output, '内部管理系统登录入口.png')});
  await context.close();
} catch (error) { failure = {type: error.name, message: error.message.slice(0, 300)}; }
finally { if (browser) await browser.close(); }
const result = {at: new Date().toISOString(), status: failure ? 'FAIL' : 'PASS', target, checks, failure,
  pageErrors, staticErrors, authenticated: false, businessWrites: 0, realNotificationCommands: 0,
  realUserCookiesUsed: false, tlsValidationEnabled: true};
await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result));
if (failure) process.exitCode = 1;
