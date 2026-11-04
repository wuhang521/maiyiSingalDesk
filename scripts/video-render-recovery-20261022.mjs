// Recover only the already-confirmed local render in the original installed app.
// No voice/scene generation, provider submit or user acceptance is performed.
import { _electron as electron, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';

const evidence='D:/maiyi/docs/delivery/maiyi-render-path-fix-20261022/evidence';
const projectId='fce6e892-687c-4f3c-8154-4467d2d45461';
const oldRender='775048f8-bef1-4981-b14a-7c88cbb79974';
const installed='D:/maiyi/signaldesk-desktop';
const digest=x=>crypto.createHash('sha256').update(x).digest('hex');
const env=Object.fromEntries(Object.entries(process.env).filter(([k])=>/^(PATH|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMFILES|PROGRAMFILES\(X86\)|PATHEXT|SYSTEMDRIVE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i.test(k)));
await fs.mkdir(evidence,{recursive:true});
let app;
try {
  app=await electron.launch({executablePath:path.join(installed,'迈医协同.exe'),env:{...env,SIGNALDESK_RELEASE_API_URL:'http://127.0.0.1:9/no-release'}});
  const main=()=>app.windows().find(p=>{try {const u=new URL(p.url());return u.pathname.endsWith('/index.html')&&u.searchParams.get('widget')!=='1';}catch{return false;}});
  await expect.poll(()=>Boolean(main()),{timeout:45000}).toBe(true);
  const page=main();
  const win=await app.browserWindow(page);
  await win.evaluate(w=>{w.webContents.setBackgroundThrottling(false);if(w.isMinimized())w.restore();w.show();w.focus();});
  await expect(page.getByRole('button',{name:'内容生成',exact:true})).toBeVisible({timeout:45000});
  const proof=await page.evaluate(async ({projectId,oldRender})=>{
    const session=await window.signalDesk.loadAuthSession();
    const c=await window.signalDesk.getVideoConnection({backendUrl:session.backendUrl,accessToken:session.accessToken});
    if(c.identityHash!=='c521cfb485bede105cfbf8895352ef3ecd80e792ed408871409272f5443a4d0f')throw new Error('Account mismatch');
    const get=async(route,body)=>{
      const r=await fetch(c.baseUrl+'/api/v1/video/projects/'+projectId+route,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+c.accessToken,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{}),redirect:'error'});
      if(!r.ok)throw new Error('Local render API failed '+r.status);
      return r.json();
    };
    const p=await get('');
    if(p.revision!==2||p.mode!=='standard'||p.jobs.find(j=>j.id===oldRender)?.status!=='blocked')throw new Error('Original project changed; stopped');
    for(const stage of ['inputs','voice','generation'])if(p.reviews[stage]?.revision!==2||!['approved','skipped'].includes(p.reviews[stage]?.decision))throw new Error('Existing confirmation missing');
    const media=p.jobs.filter(j=>j.revision===2&&['voice','scene','scene_batch','avatar'].includes(j.kind));
    const mediaFingerprint=JSON.stringify(media);
    // Stable key: repeating this repair reads the same free render job.
    const job=await get('/jobs',{kind:'render',expected_revision:2,idempotency_key:'local-render-path-recovery-20261022-fce6e892'});
    return {jobId:job.id,mediaFingerprint,title:p.plan?.brief?.title||p.topic,original_job_count:p.jobs.length};
  },{projectId,oldRender});
  console.log('CHECK: original confirmations retained; only local render requested');
  let final;
  for(let i=0;i<90;i++){
    final=await page.evaluate(async ({projectId,jobId})=>{
      const s=await window.signalDesk.loadAuthSession(),c=await window.signalDesk.getVideoConnection({backendUrl:s.backendUrl,accessToken:s.accessToken});
      const r=await fetch(c.baseUrl+'/api/v1/video/projects/'+projectId+'/jobs/'+jobId,{headers:{Authorization:'Bearer '+c.accessToken},redirect:'error'});
      if(!r.ok)throw new Error('Read local render failed '+r.status);
      return r.json();
    },{projectId,jobId:proof.jobId});
    if(['succeeded','blocked','failed','needs_attention'].includes(final.status))break;
    await new Promise(r=>setTimeout(r,1000));
  }
  if(final?.status!=='succeeded')throw new Error('Local render did not succeed: '+final?.status);
  const audit=await page.evaluate(async ({projectId,fingerprint,jobId})=>{
    const s=await window.signalDesk.loadAuthSession(),c=await window.signalDesk.getVideoConnection({backendUrl:s.backendUrl,accessToken:s.accessToken});
    const p=await(await fetch(c.baseUrl+'/api/v1/video/projects/'+projectId,{headers:{Authorization:'Bearer '+c.accessToken},redirect:'error'})).json();
    const media=p.jobs.filter(j=>j.revision===2&&['voice','scene','scene_batch','avatar'].includes(j.kind));
    return {original_media_jobs_unchanged:JSON.stringify(media)===fingerprint,video_acceptance_not_performed:p.reviews.video?.decision!=='approved',project_status:p.status,additional_jobs:p.jobs.filter(j=>j.id===jobId).map(j=>({id:j.id,kind:j.kind,status:j.status}))};
  },{projectId,fingerprint:proof.mediaFingerprint,jobId:proof.jobId});
  if(!audit.original_media_jobs_unchanged||!audit.video_acceptance_not_performed)throw new Error('Media or acceptance unexpectedly changed');
  await page.getByRole('button',{name:'内容生成',exact:true}).click();
  await page.getByRole('tab',{name:'视频制作',exact:true}).click();
  const title=proof.title.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  await page.getByRole('button',{name:new RegExp(title)}).first().click();
  await page.getByRole('button',{name:'查看生成视频',exact:true}).first().click();
  const video=page.getByLabel('生成视频预览',{exact:true});
  await expect(video).toBeVisible();
  await expect.poll(()=>video.evaluate(v=>Number.isFinite(v.duration)&&v.duration>0&&v.readyState>=2),{timeout:30000}).toBe(true);
  await video.evaluate(v=>{v.muted=true;return v.play();});
  await expect.poll(()=>video.evaluate(v=>v.currentTime),{timeout:15000}).toBeGreaterThan(.25);
  const playback=await video.evaluate(v=>{v.pause();return {duration:v.duration,current_time:v.currentTime,ready_state:v.readyState,error:v.error?.code??null,muted:v.muted};});
  if(playback.error!==null)throw new Error('Installed video preview failed');
  await expect(page.getByRole('button',{name:'确认成片验收',exact:true})).toBeEnabled();
  const png=await win.evaluate(async w=>(await w.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG().toString('base64'));
  await fs.writeFile(path.join(evidence,'installed-final-video.png'),Buffer.from(png,'base64'));
  const report={status:'PASS',observed_at:new Date().toISOString(),project_id:projectId,revision:2,old_render_job_id:oldRender,new_render_job_id:final.id,result:final.result,original_media_jobs_sha256:digest(proof.mediaFingerprint),...audit,playback,paid_provider_calls:0,local_render_jobs_created:1,app_asar_sha256:digest(await fs.readFile(path.join(installed,'resources/app.asar')))};
  await fs.writeFile(path.join(evidence,'installed-render-recovery.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({status:report.status,new_render_job_id:report.new_render_job_id,duration:final.result.duration,exports:final.result.exports.length,...audit,playback,paid_provider_calls:0}));
}finally{if(app)await app.close();}
