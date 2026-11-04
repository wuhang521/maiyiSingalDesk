// Real old/new Electron applications, isolated userData and synthetic local account.
// This exercises program-file replacement and data compatibility, not the NSIS wizard.
import { _electron as electron, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';

const cwd=process.cwd(), evidence='D:/maiyi/docs/delivery/maiyi-autoupdate-release-20261023/evidence';
const scratch=await fs.mkdtemp('D:/maiyi/tmp/maiyi-upgrade-check-');
const profile=path.join(scratch,'profile');
const roaming=path.join(scratch,'Roaming'), local=path.join(scratch,'Local');
await Promise.all([fs.mkdir(roaming,{recursive:true}),fs.mkdir(local,{recursive:true})]);
const config=path.join(profile,'video-production/config/video-config.json');
await fs.mkdir(path.dirname(config),{recursive:true});
await fs.mkdir(evidence,{recursive:true});
await fs.writeFile(config,JSON.stringify({TASK_VIDEO_PYTHON:'Z:/old-computer/python.exe',TASK_VIDEO_FFMPEG:'Z:/old-computer/ffmpeg.exe',TASK_VIDEO_PAID_ENABLED:'0',TASK_VIDEO_SCENE_PROVIDER:'seedance',TASK_VIDEO_COST_REVIEW_REQUIRED:'0'}));
const bytesHash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const configHash=bytesHash(await fs.readFile(config));
const user={id:17,username:'upgrade-test',display_name:'升级测试',staff_id:17,role:'member'};
const server=http.createServer((req,res)=>{
  res.setHeader('Access-Control-Allow-Origin','*');res.setHeader('Access-Control-Allow-Headers','Authorization,Content-Type,X-SignalDesk-Operator');
  if(req.method==='OPTIONS'){res.writeHead(204);res.end();return;}
  let body=[];req.on('data',x=>body.push(x));req.on('end',()=>{
    const route=new URL(req.url,'http://127.0.0.1').pathname;
    let data=[];
    if(route==='/api/v1/auth/login')data={access_token:'synthetic-upgrade-account-token',expires_at:'2099-01-01T00:00:00',user};
    else if(route==='/api/v1/auth/me')data=user;
    else if(route.includes('/articles')||route==='/api/v1/tasks')data={items:[],total:0};
    else if(route==='/api/v1/content/schema')data={account_fields:[],episode_fields:[],steps:[],current_user:{id:17,name:'升级测试'}};
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));
  });
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const backend='http://127.0.0.1:'+server.address().port;
const env=Object.fromEntries(Object.entries(process.env).filter(([k])=>/^(PATH|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMFILES|PROGRAMFILES\(X86\)|PATHEXT|SYSTEMDRIVE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i.test(k)));
const report={started_at:new Date().toISOString(),scratch,synthetic_profile_only:true,original_client_stopped:false,actual_nsis_installer_executed:false,cloud_generation_requests:0};
let app;
async function launch(executable){
  app=await electron.launch({executablePath:executable,env:{...env,APPDATA:roaming,LOCALAPPDATA:local,SIGNALDESK_E2E_USER_DATA:profile,SIGNALDESK_BACKEND_URL:backend}});
  const main=()=>app.windows().find(p=>{try{const u=new URL(p.url());return u.pathname.endsWith('/index.html')&&u.searchParams.get('widget')!=='1';}catch{return false;}});
  await expect.poll(()=>Boolean(main()),{timeout:45000}).toBe(true);
  const page=main(), win=await app.browserWindow(page);
  await win.evaluate(w=>{w.webContents.setBackgroundThrottling(false);w.show();});
  return {page,win};
}
async function snapshot(page,name){
  await page.evaluate(async()=>{await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
  await page.waitForTimeout(300);
  const win=await app.browserWindow(page);
  const png=await win.evaluate(async w=>(await w.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG().toString('base64'));
  await fs.writeFile(path.join(evidence,name),Buffer.from(png,'base64'));
}
async function localProject(page,id){
  return page.evaluate(async id=>{
    const s=await window.signalDesk.loadAuthSession();
    const c=await window.signalDesk.getVideoConnection({backendUrl:s.backendUrl,accessToken:s.accessToken});
    const r=await fetch(c.baseUrl+'/api/v1/video/projects'+(id?'/'+id:''),{method:id?'GET':'POST',headers:{Authorization:'Bearer '+c.accessToken,'Content-Type':'application/json'},...(id?{}:{body:JSON.stringify({topic:'升级前保存的视频项目',mode:'standard',platforms:['视频号','抖音','小红书']})}),redirect:'error'});
    if(!r.ok)throw new Error('Local project request failed '+r.status);
    const p=await r.json();
    return {id:p.id,revision:p.revision,topic:p.topic,platforms:p.platforms,plan:p.plan,jobs:p.jobs};
  },id);
}
try{
  const old=await launch('D:/maiyi/signaldesk-desktop/迈医协同.exe');
  await expect(old.page.getByRole('heading',{name:'登录工作台'})).toBeVisible({timeout:30000});
  await old.page.getByLabel('账号',{exact:true}).fill('upgrade-test');
  await old.page.getByLabel('密码',{exact:true}).fill('synthetic-password');
  await old.page.getByRole('button',{name:'继续',exact:true}).click();
  await expect(old.page.getByRole('button',{name:'设置',exact:true})).toBeVisible({timeout:30000});
  const oldInfo=await old.page.evaluate(()=>window.signalDesk.getAppInfo());
  if(oldInfo.version!=='0.5.3')throw new Error('Old application is not the expected baseline');
  await old.page.evaluate(()=>localStorage.setItem('maiyi-upgrade-sentinel','已有收藏和配置的隔离保留标记'));
  const originalProject=await localProject(old.page);
  const loginBefore=bytesHash(await fs.readFile(path.join(profile,'auth-session.json')));
  await app.close();app=null;
  // A new unpacked release reads the same profile. No original installation is modified.
  const current=await launch(path.join(cwd,'release/win-unpacked/迈医协同.exe'));
  await expect(current.page.getByRole('button',{name:'设置',exact:true})).toBeVisible({timeout:45000});
  const appInfo=await current.page.evaluate(()=>window.signalDesk.getAppInfo());
  if(appInfo.version!=='0.6.0')throw new Error('New release version mismatch');
  const sentinel=await current.page.evaluate(()=>localStorage.getItem('maiyi-upgrade-sentinel'));
  if(sentinel!=='已有收藏和配置的隔离保留标记')throw new Error('Browser storage did not survive program upgrade');
  const restored=await localProject(current.page,originalProject.id);
  if(JSON.stringify(restored)!==JSON.stringify(originalProject))throw new Error('Video project changed during upgrade');
  const capability=await current.page.evaluate(async()=>{
    const s=await window.signalDesk.loadAuthSession(),c=await window.signalDesk.getVideoConnection({backendUrl:s.backendUrl,accessToken:s.accessToken});
    const r=await fetch(c.baseUrl+'/api/v1/video/projects/capabilities',{headers:{Authorization:'Bearer '+c.accessToken},redirect:'error'});
    if(!r.ok)throw new Error('Bundled capability failed');
    const body=await r.json();
    return {render_ready:body.render?.ready,voice_ready:body.voice?.ready,scene_ready:body.scene?.ready,cost_review_required:body.cost_review_required};
  });
  if(capability.render_ready!==true||capability.voice_ready!==false||capability.scene_ready!==false)throw new Error('Portable toolchain or missing-key behavior wrong');
  await current.page.getByRole('button',{name:'设置',exact:true}).click();
  await expect(current.page.getByText('版本 0.6.0',{exact:true})).toBeVisible();
  await expect(current.page.getByText('更新会保留已有账号、数据和配置。安装前会请你确认重启。',{exact:true})).toBeVisible();
  await current.page.getByText('版本 0.6.0',{exact:true}).scrollIntoViewIfNeeded();
  await snapshot(current.page,'packaged-settings-1440.png');
  await current.win.evaluate(w=>w.setSize(1080,760));
  await snapshot(current.page,'packaged-settings-1080.png');
  const newLogin=bytesHash(await fs.readFile(path.join(profile,'auth-session.json')));
  if(loginBefore!==newLogin||configHash!==bytesHash(await fs.readFile(config)))throw new Error('Saved private config or encrypted login changed');
  report.status='PASS';report.old_version=oldInfo.version;report.new_version=appInfo.version;
  report.old_machine_tool_paths_overridden=true;report.encrypted_login_unchanged=true;report.config_unchanged=true;report.browser_storage_preserved=true;report.project_preserved=true;report.capability=capability;
  console.log(JSON.stringify(report));
}finally{
  if(app)await app.close();
  await new Promise(r=>server.close(r));
  await fs.writeFile(path.join(evidence,'packaged-upgrade.json'),JSON.stringify(report,null,2));
}
