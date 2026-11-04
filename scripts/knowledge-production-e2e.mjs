// Final packaged client -> SSH tunnel -> actual upgraded production API/worker.
import { _electron as electron, expect } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
const [credentialFile, base, output, packaged] = process.argv.slice(2);
const login = JSON.parse(await readFile(credentialFile, 'utf8'));
await mkdir(output, { recursive: true });const run = await mkdtemp(path.join(output, 'run-'));
let app;const errors=[];let token;
async function api(url, method='GET', value, headers={}) {
  const r=await fetch(base+'/api/v1'+url,{method,headers:{Authorization:'Bearer '+token,...(value&&typeof value==='object'&&!Buffer.isBuffer(value)?{'Content-Type':'application/json'}:{}),...headers},body:value?(Buffer.isBuffer(value)?value:JSON.stringify(value)):undefined});
  if(!r.ok)throw new Error('Actual API returned '+r.status+' for '+url.split('?')[0]);return r.status===204?null:r.json();
}
try {
  const response=await fetch(base+'/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:login.username,password:login.password})});
  expect(response.status).toBe(200);token=(await response.json()).access_token;
  const caps=await api('/knowledge/capabilities');
  for(const key of ['cloud_asr_enabled','ocr_enabled','organization_enabled','video_ocr_enabled','semantic_enabled','teams_available','notifications_available','media_merge_available','costs_available','cloud_qa_enabled'])expect(caps[key],key).toBe(true);
  expect(caps.cloud_asr_provider).toContain('千问');expect(caps.semantic_provider).toContain('千问');
  app=await electron.launch({executablePath:path.join(packaged,'迈医协同.exe'),env:{...process.env,SIGNALDESK_E2E_USER_DATA:path.join(run,'profile'),SIGNALDESK_BACKEND_URL:base,SIGNALDESK_RELEASE_API_URL:base+'/release-check-disabled'}});
  const page=await app.firstWindow();page.on('pageerror',error=>errors.push(error.message));
  await app.evaluate(({BrowserWindow})=>{for(const window of BrowserWindow.getAllWindows()){window.webContents.setBackgroundThrottling(false);window.show();window.focus();}});
  await page.bringToFront();
  await page.route('**/*',route=>/^https?:/.test(route.request().url())&&!route.request().url().startsWith(base+'/')?route.abort():route.continue());
  await page.getByLabel('账号',{exact:true}).fill(login.username);await page.getByLabel('密码',{exact:true}).fill(login.password);await page.getByRole('button',{name:'继续',exact:true}).click();
  await page.getByRole('button',{name:'知识库',exact:true}).click();
  await page.getByRole('button',{name:'收集资料',exact:true}).click();
  await page.getByLabel('资料标题（选填）').fill('正式发布合成验收笔记');
  await page.getByLabel('正文 / 转写文字（选填）').fill('正式发布验收仅使用合成资料。知识库归档需要保存原始来源，核对当前正文，并让问答引用可以追溯。此内容不是客户资料。');
  await page.getByRole('button',{name:'保存到知识库'}).click();
  await expect(page.getByRole('article',{name:'知识资料详情'}).getByRole('heading',{name:'正式发布合成验收笔记',exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'知识问答',exact:true}).click();await page.getByLabel('问题或关键词').fill('正式发布验收');await page.getByRole('button',{name:'查找依据'}).click();
  await expect(page.getByRole('button',{name:/正式发布合成验收笔记.*版本/})).toBeVisible();
  await page.setViewportSize({width:1440,height:920});await page.bringToFront();await page.screenshot({path:path.join(output,'actual-production-citation.png')});
  const content=Buffer.from('正式发布原件合成验收：保存原始来源并核对正文。此文件不含任何客户资料。');
  const captured=await api('/knowledge/capture/files','POST',content,{'Content-Type':'application/octet-stream','X-Content-SHA256':createHash('sha256').update(content).digest('hex'),'Idempotency-Key':createHash('sha256').update(randomUUID()).digest('hex'),'X-Filename-Base64':Buffer.from('发布验收原件.txt').toString('base64')});
  await expect.poll(async()=> (await api('/knowledge/items/'+captured.item.id)).status,{timeout:30000}).toBe('ready');
  const file=await api('/knowledge/items/'+captured.item.id);expect(file.content).toContain('不含任何客户资料');
  const listed=await api('/knowledge/items');
  for(const item of listed.items){const detail=await api('/knowledge/items/'+item.id);const removed=await api('/knowledge/items/'+item.id+'?revision='+detail.revision,'DELETE');await api('/knowledge/items/'+item.id+'/purge','POST',{revision:removed.revision,confirmation:'永久删除'});}
  expect((await api('/knowledge/items')).items).toHaveLength(0);expect(errors).toEqual([]);
  await api('/auth/logout','POST');
  const result={passed:true,packaged_version:'0.6.1',actual_production_api:true,actual_mysql:true,actual_worker:true,provider_simulation:false,
    checks:['all configured capabilities ready','personal login','note persistence','extractive answer opens original citation','actual streamed original capture and worker parsing','synthetic originals purged'],
    paid_calls:0,external_message_sent:false,synthetic_only:true,renderer_errors:errors};
  await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
} finally {if(app)await app.close();}
