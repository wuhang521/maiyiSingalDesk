// Real program replacement and actual local KB API; never starts NSIS or user install.
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
const options=new Map();for(let i=2;i<process.argv.length;i+=2){if(!process.argv[i]?.startsWith('--')||!process.argv[i+1])throw new Error('Use --new-package-dir PATH --expected-new-version VERSION --source-root PATH');options.set(process.argv[i],process.argv[i+1]);}
const BASE=path.resolve(options.get('--source-root')||'D:/maiyi/tmp/k62-src');const require=createRequire(path.join(BASE,'package.json'));
const {_electron:electron,expect}=require('@playwright/test');
const ROOT=path.resolve('D:/maiyi/tmp/kb-install-acceptance');const OUT='D:/maiyi/docs/delivery/knowledge-remaining-acceptance-20261028/install';
const prepared=JSON.parse(await fs.readFile(path.join(ROOT,'program-upgrade-input.json'),'utf8'));
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const newPackage=path.resolve(options.get('--new-package-dir')||prepared.new||'');
const expectedNewVersion=options.get('--expected-new-version')||prepared.expected_new_version||prepared.new_version;
if(!expectedNewVersion||!/^\d+\.\d+\.\d+$/.test(expectedNewVersion))throw new Error('Explicit expected new version required');
const publishedVersion=JSON.parse(require('@electron/asar').extractFile(path.join(newPackage,'resources/app.asar'),'package.json')).version;
if(publishedVersion!==expectedNewVersion)throw new Error('New package version does not match explicit expectation');
async function inventory(folder){const map={};async function walk(dir){for(const item of await fs.readdir(dir,{withFileTypes:true})){const file=path.join(dir,item.name);if(item.isSymbolicLink())throw new Error('Symlink in program input');if(item.isDirectory())await walk(file);else{const stat=await fs.stat(file);map[path.relative(folder,file).replaceAll('\\','/')]={bytes:stat.size,sha256:hash(await fs.readFile(file))};}}}await walk(folder);return map;}
if(path.resolve(prepared.new||'.')!==newPackage||prepared.new_version!==expectedNewVersion){prepared.new_files=await inventory(newPackage);}
prepared.new=newPackage;prepared.new_version=expectedNewVersion;
const programRun=path.resolve(prepared.run),active=path.resolve(prepared.active);if(!prepared.prepared||!active.startsWith(ROOT+path.sep))throw new Error('Unowned test program directory');
const run=await fs.mkdtemp(path.join(programRun,'attempt-'));
const profile=path.join(run,'profile'),roaming=path.join(run,'Roaming'),local=path.join(run,'Local');await Promise.all([fs.mkdir(profile,{recursive:true}),fs.mkdir(roaming,{recursive:true}),fs.mkdir(local,{recursive:true}),fs.mkdir(OUT,{recursive:true})]);
const server=path.join(run,'server');await fs.mkdir(server);await fs.cp(path.join(BASE,'server/app'),path.join(server,'app'),{recursive:true});
const data=path.join(run,'data');await fs.mkdir(data);const storage=path.join(data,'originals');await fs.mkdir(storage);
const envFile=['TASK_DATABASE_URL=sqlite:///'+path.join(data,'knowledge.db').replaceAll('\\','/'),'TASK_API_KEY=synthetic-program-rollback','TASK_KNOWLEDGE_STORAGE_DIR='+storage.replaceAll('\\','/'),'TASK_KNOWLEDGE_ASR_ENABLED=false','TASK_KNOWLEDGE_CLOUD_ASR_ENABLED=false','TASK_KNOWLEDGE_OCR_ENABLED=false','TASK_KNOWLEDGE_CLOUD_QA_ENABLED=false','TASK_KNOWLEDGE_SEMANTIC_ENABLED=false','TASK_KNOWLEDGE_ORGANIZATION_ENABLED=false','TASK_KNOWLEDGE_DISPATCH_ENABLED=false','TASK_KNOWLEDGE_GATEWAY_SECRET=','TASK_KNOWLEDGE_NOTIFICATION_WEBHOOK_BASE=','TASK_KNOWLEDGE_NOTIFICATION_WEBHOOK_SECRET=','TASK_PROMPT_API_KEY=','TASK_HERMES_WEBHOOK_URL='].join('\n')+'\n';await fs.writeFile(path.join(server,'.env'),envFile);
const socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));const base='http://127.0.0.1:'+port;
const python='D:/maiyi/SignalDesk-源码交接-2026-09-16/SignalDesk-源码交接/.venv-signaldesk/Scripts/python.exe';
const childEnv=Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('TASK_')&&!key.startsWith('PADDLE_')&&!['PYTHONPATH','PYTHONHOME','ELECTRON_RUN_AS_NODE'].includes(key)));
let diagnostic='';const backend=spawn(python,[path.join(ROOT,'rollback_server.py'),'--root',server,'--port',String(port)],{cwd:server,env:{...childEnv,PYTHONIOENCODING:'utf-8',PYTHONDONTWRITEBYTECODE:'1'},windowsHide:true,stdio:['ignore','pipe','pipe']});backend.stdout.on('data',b=>{diagnostic+=b;});backend.stderr.on('data',b=>{diagnostic+=b;});
const config=path.join(profile,'video-production/config/video-config.json');await fs.mkdir(path.dirname(config),{recursive:true});await fs.writeFile(config,JSON.stringify({TASK_VIDEO_PAID_ENABLED:'0',TASK_VIDEO_COST_REVIEW_REQUIRED:'1'}));const configHash=hash(await fs.readFile(config));
const sentinel='knowledge-upgrade-rollback-synthetic';const original=Buffer.from('程序升级保留原件：保存来源和原始附件。');
let app,page,token,note,uploaded,fileId,loginHash;const phase=[];const errors=[];const checks=[];const exitProofs=[];let copyRetries=0;
const report={passed:false,old_version:'0.6.1',new_version:expectedNewVersion,restored_version:'0.6.1',source_root:BASE,new_package:newPackage,scratch:run,real_nsis_executed:false,original_installation_touched:false,production_changed:false,qq_sent:false,cloud_requests:0};
async function api(url,method='GET',value){const r=await fetch(base+'/api/v1'+url,{method,headers:{Authorization:'Bearer '+token,...(value?{'Content-Type':'application/json'}:{})},body:value?JSON.stringify(value):undefined});if(!r.ok)throw new Error('Local API '+r.status+' '+url);return r.status===204?null:r.json();}
async function open(expected){
 const env=Object.fromEntries(Object.entries(childEnv).filter(([key])=>/^(PATH|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMFILES|PROGRAMFILES\(X86\)|PATHEXT|SYSTEMDRIVE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i.test(key)));
 app=await electron.launch({executablePath:path.join(active,'迈医协同.exe'),env:{...env,APPDATA:roaming,LOCALAPPDATA:local,SIGNALDESK_E2E_USER_DATA:profile,SIGNALDESK_BACKEND_URL:base,SIGNALDESK_RELEASE_API_URL:base+'/disabled-release'}});
 await app.evaluate(({session},url)=>{session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(details,callback)=>callback({cancel:!details.url.startsWith(url+'/')}));},base);
 await expect.poll(()=>app.windows().find(p=>p.url().includes('index.html')&&!p.url().includes('widget=1'))?.url(),{timeout:30000}).toBeTruthy();page=app.windows().find(p=>p.url().includes('index.html')&&!p.url().includes('widget=1'));
 page.on('pageerror',error=>errors.push(error.message));await page.route('**/*',route=>/^https?:/.test(route.request().url())&&!route.request().url().startsWith(base+'/')?route.abort():route.continue());
 const info=await page.evaluate(()=>window.signalDesk.getAppInfo());expect(info.version).toBe(expected);const actualUserData=await app.evaluate(({app})=>app.getPath('userData'));expect(path.resolve(actualUserData)).toBe(profile);
 await app.evaluate(({BrowserWindow})=>{for(const w of BrowserWindow.getAllWindows()){w.webContents.setBackgroundThrottling(false);w.show();w.focus();}});phase.push({version:info.version,profile_isolated:true});
}
async function waitOwnedExit(){
 const prefix=(active+path.sep).replaceAll('\\','\\\\');
 const query="SELECT ProcessId FROM Win32_Process WHERE ExecutablePath LIKE '"+prefix+"%'";
 const quote=value=>"'"+value.replaceAll("'","''")+"'";
 const command="$ErrorActionPreference='Stop'; $owned=@(Get-CimInstance -Query "+quote(query)+"); [Console]::WriteLine($owned.Count)";
 let initial;
 for(let attempt=0;attempt<30;attempt++){
  const count=await new Promise((resolve,reject)=>{const child=spawn('powershell.exe',['-NoProfile','-NonInteractive','-Command',command],{windowsHide:true,stdio:['ignore','pipe','pipe']});let output='',error='';child.stdout.on('data',value=>{output+=value;});child.stderr.on('data',value=>{error+=value;});child.on('error',reject);child.on('exit',code=>code===0&&/^\d+\s*$/.test(output)?resolve(Number(output.trim())):reject(new Error('Owned-program read-only PID query failed: '+error.slice(-220))));});
  initial??=count;
  if(count===0){exitProofs.push({passed:true,scope:'owned scratch executable path only',initial_owned_pids:initial,remaining_owned_pids:0,processes_killed:0,wait_iterations:attempt});return;}
  await new Promise(resolve=>setTimeout(resolve,500));
 }
 throw new Error('Owned program process did not exit; no process killed');
}
async function close(){if(app){const child=app.process();await app.close();app=null;await new Promise((resolve,reject)=>{if(child.exitCode!==null)return resolve();const timer=setTimeout(()=>reject(new Error('Owned Electron child did not exit')),15000);child.once('exit',()=>{clearTimeout(timer);resolve();});if(child.exitCode!==null){clearTimeout(timer);resolve();}});await waitOwnedExit();}}
async function copyUnlocked(source,target){
 for(let attempt=0;attempt<30;attempt++){
  try{await fs.copyFile(source,target);return;}catch(error){if(!['EBUSY','EPERM','EACCES'].includes(error.code)||attempt===29)throw error;copyRetries++;await new Promise(resolve=>setTimeout(resolve,500));}
 }
}
async function capture(name){const png=await app.evaluate(async({BrowserWindow})=>{const w=BrowserWindow.getAllWindows()[0];w.setBounds({width:1440,height:920});w.show();w.focus();return(await w.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG().toString('base64');});await fs.writeFile(path.join(OUT,name+'.png'),Buffer.from(png,'base64'));}
async function verifyPayload(map){for(const [name,row]of Object.entries(map)){const file=path.resolve(active,...name.split('/'));if(!file.startsWith(active+path.sep))throw new Error('Unsafe member');const stat=await fs.lstat(file);if(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1||stat.size!==row.bytes||hash(await fs.readFile(file))!==row.sha256)throw new Error('Program member mismatch '+name);}}
async function replace(source,map){
 if(app)throw new Error('Application must be closed before file replacement');
 const targetSet=new Set(Object.keys(map));const currentMap=phase.length===1?prepared.old_files:prepared.new_files;
 for(const [name,row]of Object.entries(map)){
  const target=path.resolve(active,...name.split('/'));if(!target.startsWith(active+path.sep))throw new Error('Unsafe target');
  if(currentMap[name]?.sha256===row.sha256)continue;
  const input=path.resolve(source,...name.split('/'));if(!input.startsWith(path.resolve(source)+path.sep))throw new Error('Unsafe source');
  const stat=await fs.lstat(target).catch(()=>null);if(stat&&(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1))throw new Error('Unsafe existing target');
  await fs.mkdir(path.dirname(target),{recursive:true});await copyUnlocked(input,target);
 }
 for(const name of Object.keys(currentMap)){if(targetSet.has(name))continue;const target=path.resolve(active,...name.split('/'));if(!target.startsWith(active+path.sep))throw new Error('Unsafe deletion');const stat=await fs.lstat(target);if(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1)throw new Error('Unsafe removed member');await fs.unlink(target);}
 await verifyPayload(map);
}
async function retained(){
 await expect(page.getByRole('button',{name:'知识库',exact:true})).toBeVisible({timeout:30000});await page.getByRole('button',{name:'知识库',exact:true}).click();
 expect(await page.evaluate(()=>localStorage.getItem('kb-upgrade-sentinel'))).toBe(sentinel);
 const session=await page.evaluate(()=>window.signalDesk.loadAuthSession());token=session.accessToken;expect(session.user.username).toBe('rollback-test');
 expect(hash(await fs.readFile(path.join(profile,'auth-session.json')))).toBe(loginHash);expect(hash(await fs.readFile(config))).toBe(configHash);
 const detail=await api('/knowledge/items/'+note.id);expect(detail.content).toContain('程序升级保留资料');
 const r=await fetch(base+'/api/v1/knowledge/assets/'+uploaded.assets[0].id,{headers:{Authorization:'Bearer '+token}});expect(r.status).toBe(200);expect(hash(Buffer.from(await r.arrayBuffer()))).toBe(hash(original));
 await page.getByRole('tab',{name:'知识问答',exact:true}).click();await page.getByLabel('问题或关键词').fill('程序升级保留资料');await page.getByRole('button',{name:'查找依据'}).click();const citation=page.getByRole('button',{name:/程序升级保留资料.*版本/});await expect(citation).toBeVisible();await citation.click();await expect(page.getByRole('article',{name:'知识资料详情'}).getByRole('heading',{name:note.title,exact:true})).toBeVisible();
 return detail;
}
try{
 await expect.poll(async()=>{if(backend.exitCode!==null)throw new Error('Local backend stopped');try{return(await fetch(base+'/health')).status;}catch{return 0;}},{timeout:30000}).toBe(200);
 await verifyPayload(prepared.old_files);await open('0.6.1');await expect(page.getByRole('heading',{name:'登录工作台'})).toBeVisible();await page.getByLabel('账号',{exact:true}).fill('rollback-test');await page.getByLabel('密码',{exact:true}).fill('synthetic-only-password');await page.getByRole('button',{name:'继续',exact:true}).click();await page.getByRole('button',{name:'知识库',exact:true}).click();
 await page.evaluate(value=>localStorage.setItem('kb-upgrade-sentinel',value),sentinel);
 const session=await page.evaluate(()=>window.signalDesk.loadAuthSession());token=session.accessToken;
 await page.getByRole('button',{name:'收集资料',exact:true}).click();await page.getByLabel('资料标题（选填）').fill('程序升级保留资料');await page.getByLabel('正文 / 转写文字（选填）').fill('程序升级保留资料：原始版本保存来源及引用。');await page.getByRole('button',{name:'保存到知识库'}).click();await expect(page.getByRole('article',{name:'知识资料详情'}).getByRole('heading',{name:'程序升级保留资料',exact:true})).toBeVisible();
 await page.getByLabel('上传知识文件').setInputFiles({name:'程序升级保留原件.txt',mimeType:'text/plain',buffer:original});
 await expect.poll(async()=>{const list=await api('/knowledge/items');return list.items.find(x=>x.title==='程序升级保留原件.txt')?.status;},{timeout:30000}).toBe('ready');
 const list=await api('/knowledge/items');note=list.items.find(x=>x.title==='程序升级保留资料');fileId=list.items.find(x=>x.title==='程序升级保留原件.txt').id;uploaded=await api('/knowledge/items/'+fileId);loginHash=hash(await fs.readFile(path.join(profile,'auth-session.json')));await capture('program-061-before');checks.push('0.6.1 owns a newly copied program directory and synthetic profile; real UI login, note and original upload persist');await close();
 await replace(prepared.new,prepared.new_files);await open(expectedNewVersion);await retained();await capture('program-'+expectedNewVersion+'-after-upgrade');checks.push('all '+expectedNewVersion+' program file hashes match published payload after in-place replacement; encrypted login/config/localStorage/original/citation retained');
 await page.getByRole('article',{name:'知识资料详情'}).getByRole('button',{name:'补充 / 修订正文',exact:true}).click();await page.getByLabel('正文 / 转写文字').fill('程序升级保留资料：'+expectedNewVersion+' 新增修订必须在旧版回退后保留。');await page.getByRole('button',{name:'保存修订',exact:true}).click();await expect.poll(async()=>(await api('/knowledge/items/'+note.id)).content).toContain(expectedNewVersion+' 新增修订');await close();
 await replace(prepared.old,prepared.old_files);await open('0.6.1');const restored=await retained();expect(restored.content).toContain(expectedNewVersion+' 新增修订');await capture('program-061-after-rollback');checks.push('all restored 0.6.1 program file hashes match original payload; newer revision is readable and cited after rollback');
 expect((await api('/knowledge/items')).items).toHaveLength(2);expect((await api('/knowledge/usage')).daily_submissions).toBe(0);expect(errors).toEqual([]);report.passed=true;report.phase=phase;report.checks=checks;report.original_sha256=hash(original);report.profile_persisted=true;report.encrypted_login_unchanged=true;report.config_unchanged=true;report.new_data_retained_on_rollback=true;report.program_file_replacement_executed=true;report.restored_program_files=Object.keys(prepared.old_files).length;report.bounded_copy_retries=copyRetries;report.owned_process_exit_proofs=exitProofs;
}catch(error){report.error_type=error.name;report.detail=String(error.message).slice(0,350);throw error;}
finally{await close();backend.kill();await fs.writeFile(path.join(OUT,'program-upgrade-rollback.json'),JSON.stringify(report,null,2));await fs.writeFile(path.join(OUT,'program-local-backend.log'),diagnostic);console.log(JSON.stringify({passed:report.passed,phases:phase.length,checks:checks.length,real_nsis_executed:false,original_installation_touched:false}));}
