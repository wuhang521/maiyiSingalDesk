import { _electron as electron, expect } from '@playwright/test';
import { mkdir,mkdtemp,readFile,writeFile } from 'node:fs/promises';
import path from 'node:path';
const [credentialFile,base,output,packaged,sample]=process.argv.slice(2);
const login=JSON.parse(await readFile(credentialFile,'utf8'));await mkdir(output,{recursive:true});const run=await mkdtemp(path.join(output,'run-'));
let app,token;const errors=[];
async function api(url,method='GET',value){const r=await fetch(base+'/api/v1'+url,{method,headers:{Authorization:'Bearer '+token,...(value?{'Content-Type':'application/json'}:{})},body:value?JSON.stringify(value):undefined});if(!r.ok)throw new Error('API '+r.status+' '+url);return r.status===204?null:r.json();}
async function capture(name){
 const png=await app.evaluate(async({BrowserWindow})=>{const w=BrowserWindow.getAllWindows()[0];w.restore();w.show();w.focus();w.setBounds({width:1440,height:920});const image=await w.webContents.capturePage();if(image.isEmpty())throw new Error('Empty window capture');return image.toPNG().toString('base64');});
 await writeFile(path.join(output,name+'.png'),Buffer.from(png,'base64'));
}
try{
 const response=await fetch(base+'/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:login.username,password:login.password})});expect(response.status).toBe(200);token=(await response.json()).access_token;
 const caps=await api('/knowledge/capabilities');expect(caps.silk_available).toBe(true);expect(caps.cloud_asr_provider).toContain('千问');
 app=await electron.launch({executablePath:path.join(packaged,'迈医协同.exe'),env:{...process.env,SIGNALDESK_E2E_USER_DATA:path.join(run,'profile'),SIGNALDESK_BACKEND_URL:base,SIGNALDESK_RELEASE_API_URL:base+'/release-check-disabled'}});
 const page=await app.firstWindow();page.on('pageerror',e=>errors.push(e.message));
 await app.evaluate(({BrowserWindow})=>{for(const w of BrowserWindow.getAllWindows()){w.webContents.setBackgroundThrottling(false);w.show();w.focus();}});
 await page.route('**/*',route=>/^https?:/.test(route.request().url())&&!route.request().url().startsWith(base+'/')?route.abort():route.continue());
 await page.getByLabel('账号',{exact:true}).fill(login.username);await page.getByLabel('密码',{exact:true}).fill(login.password);await page.getByRole('button',{name:'继续',exact:true}).click();
 await page.getByRole('button',{name:'知识库',exact:true}).click();
 await capture('native-silk-ready');
 expect(await page.getByLabel('上传知识文件').getAttribute('accept')).toContain('.aud');
 await page.getByLabel('上传知识文件').setInputFiles({name:'公开原生语音验收.aud',mimeType:'audio/silk',buffer:await readFile(sample)});
 const detail=page.getByRole('article',{name:'知识资料详情'});await expect(detail.getByRole('heading',{name:'公开原生语音验收.aud',exact:true})).toBeVisible();
 const submit=detail.getByRole('button',{name:'发送音轨并转写'});await expect(detail.getByRole('checkbox',{name:'同意发送所选素材的音轨到云端转写'})).toBeEnabled({timeout:30000});expect(await submit.isDisabled()).toBe(true);
 await detail.getByRole('checkbox',{name:'同意发送所选素材的音轨到云端转写'}).check();await submit.click();
 await expect(detail.getByRole('heading',{name:'AI 转写正文 · 待核对（暂不参与检索）'})).toBeVisible({timeout:120000});
 const listed=await api('/knowledge/items');expect(listed.items).toHaveLength(1);const id=listed.items[0].id;const draft=await api('/knowledge/items/'+id);expect(draft.method).toBe('qwen_asr_draft');expect(draft.content.length).toBeGreaterThan(0);
 expect((await api('/knowledge/ask','POST',{question:'公开原生语音验收'})).citations).toHaveLength(0);
 await capture('native-silk-draft');
 await detail.getByRole('button',{name:'已核对，入库',exact:true}).click();
 await expect.poll(async()=>(await api('/knowledge/items/'+id)).method).toBe('qwen_asr_reviewed');
 await page.getByRole('tab',{name:'知识问答',exact:true}).click();await page.getByLabel('问题或关键词').fill('公开原生语音验收');await page.getByRole('button',{name:'查找依据'}).click();
 const citation=page.getByRole('button',{name:/公开原生语音验收.*版本/});await expect(citation).toBeVisible();await citation.click();await expect(detail.getByRole('heading',{name:'公开原生语音验收.aud',exact:true})).toBeVisible();
 await capture('native-silk-reviewed-citation');expect(errors).toEqual([]);
 const result={passed:true,version:JSON.parse(await readFile(new URL('../package.json',import.meta.url),'utf8')).version,actual_packaged_client:true,actual_production_api:true,actual_mysql:true,actual_worker:true,actual_qwen:true,public_fixture:true,wechat_export_origin_verified:false,checks:['AUD file picker compatibility','explicit paid consent','actual native decode and Qwen draft','unconfirmed draft excluded','UI confirmation','time citation opens original'],paid_submissions:(await api('/knowledge/usage')).daily_submissions,qq_sent:false,renderer_errors:errors};
 await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{if(app)await app.close();}
