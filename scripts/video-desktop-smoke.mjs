// Actual Electron/source smoke against the isolated real local backend. No mocks.
import { _electron as electron, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const data = path.join(root, "artifacts/video-local-trial");
const evidence = path.join(root, "artifacts/video-desktop");
await mkdir(evidence, { recursive: true });
const login = JSON.parse(await readFile(path.join(data, "local-login.json"), "utf8"));
const safeEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => /^(PATH|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMFILES|PROGRAMFILES\(X86\)|PATHEXT|SYSTEMDRIVE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i.test(key)));
const children = [];
let app;
let page;
let stderr = "";
function start(exe, args, env) {
  const child = spawn(exe, args, {cwd:root,env:{...safeEnv,...env},windowsHide:true,stdio:["pipe","pipe","pipe"]});
  child.stderr.on("data", d => { stderr += d; });
  children.push(child);
  return child;
}
async function ready(url) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    try { const r=await fetch(url,{signal:AbortSignal.timeout(1500)}); if(r.status===200)return; } catch {}
    await new Promise(r=>setTimeout(r,250));
  }
  throw new Error("Local service startup failed: "+stderr.slice(-1500));
}
try {
  const backend = start(path.resolve(root,"../.venv-signaldesk/Scripts/python.exe"),["server/scripts/run_video_local.py",data,"18865"],{PYTHONIOENCODING:"utf-8",PYTHONDONTWRITEBYTECODE:"1"});
  backend.stdin.end(JSON.stringify({password:login.password,frontend_port:5184})+"\n");
  const vite = start(process.execPath,["node_modules/vite/bin/vite.js","--host","127.0.0.1","--port","5184","--strictPort","--mode","local-video"],{VITE_DEFAULT_BACKEND_URL:"http://127.0.0.1:18865",VITE_DEFAULT_NTFY_SERVER:"http://127.0.0.1:18865"});
  vite.stdin.end();
  await Promise.all([ready("http://127.0.0.1:18865/local-trial/status"),ready("http://127.0.0.1:5184")]);
  const state=await (await fetch("http://127.0.0.1:18865/local-trial/status")).json();
  if(state.paid_enabled || state.provider_simulation || state.production_connected) throw new Error("Smoke requires isolated unpaid real backend");
  app = await electron.launch({args:[root],env:{...safeEnv,VITE_DEV_SERVER_URL:"http://127.0.0.1:5184",SIGNALDESK_E2E_USER_DATA:path.join(data,"profile"),SIGNALDESK_BACKEND_URL:"http://127.0.0.1:18865",SIGNALDESK_RELEASE_API_URL:"http://127.0.0.1:18865/local-trial/release"}});
  page = await app.firstWindow();
  const errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  await expect(page.getByRole("button",{name:"内容生成",exact:true}).or(page.getByRole("heading",{name:"登录工作台"}))).toBeVisible({timeout:60000});
  if(await page.getByRole("heading",{name:"登录工作台"}).isVisible()) {
    await page.getByPlaceholder("请输入公司账号").fill(login.admin);
    await page.getByPlaceholder("请输入密码").fill(login.password);
    await page.getByRole("button",{name:"继续",exact:true}).click();
  }
  await page.getByRole("button",{name:"内容生成",exact:true}).click();
  await page.getByRole("tab",{name:"视频制作",exact:true}).click();
  await expect(page.getByRole("heading",{name:"给一个主题，规划整条视频"})).toBeVisible();
  await expect(page.getByRole("radio",{name:"普通 AI 视频",exact:true})).toBeChecked();
  await page.screenshot({path:path.join(evidence,"electron-video-1440.png")});
  const native = await app.browserWindow(page);
  await native.evaluate(w=>w.setSize(1080,760));
  await page.screenshot({path:path.join(evidence,"electron-video-1080.png")});
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
  if(overflow) throw new Error("Desktop document overflows minimum width");
  const caps=await page.evaluate(async()=>{
    const session=await window.signalDesk.loadAuthSession();
    const r=await fetch(session.backendUrl+"/api/v1/video/projects/capabilities",{headers:{Authorization:"Bearer "+session.accessToken}});
    return r.json();
  });
  if(caps.voice.ready || caps.scene.ready || caps.avatar.ready) throw new Error("Paid adapters unexpectedly enabled");
  if(process.env.VIDEO_EXPECT_HERMES_VOICE === "1") {
    if(!caps.voice.credentials_configured || caps.voice.credential_source !== "hermes") throw new Error("Hermes voice credential was not loaded");
    await expect(page.getByText(caps.cost_review_required === false ? "千问配音已接入 Hermes 的百炼凭证。" : "千问配音已接入 Hermes 的百炼凭证，生成时单独确认费用。",{exact:true})).toHaveCount(1);
  }
  if(process.env.VIDEO_EXPECT_HERMES_SCENE === "1") {
    if(!caps.scene.credentials_configured || caps.scene.credential_source !== "hermes") throw new Error("Hermes scene credential was not loaded");
  }
  const expectRunninghub=process.env.VIDEO_EXPECT_RUNNINGHUB_SCENE === "1";
  if(expectRunninghub) {
    if(state.scene_provider!=="runninghub" || caps.scene.provider!=="runninghub" || caps.scene.credential_type!=="SHARED" || caps.scene.credential_source!=="runninghub_shared_key") throw new Error("RunningHub shared scene route was not loaded");
    await expect(page.getByText(/普通 AI 视频使用 RunningHub/)).toHaveCount(1);
  }
  let pricingProof;
  let directProof;
  if(process.env.VIDEO_EXPECT_DIRECT_GENERATION === "1") {
    if(state.cost_review_required!==false||caps.cost_review_required!==false)throw new Error("Direct generation mode was not enabled");
    const proofTitle=`直接生成流程验证-${Date.now()}（不生成）`;
    const quoteRequests=[];
    page.on("request",request=>{if(new URL(request.url()).pathname.endsWith("/quote"))quoteRequests.push(request.url());});
    directProof=await page.evaluate(async(proofTitle)=>{
      const session=await window.signalDesk.loadAuthSession();
      async function request(route,body,method="GET") {
        const response=await fetch(session.backendUrl+"/api/v1/video/projects"+route,{method,headers:{Authorization:"Bearer "+session.accessToken,"Content-Type":"application/json"},...(body?{body:JSON.stringify(body)}:{})});
        if(!response.ok)throw new Error("Local direct-flow metadata check failed "+response.status);
        return response.json();
      }
      let project=await request("",{topic:"太阳为什么发光（直接生成界面自检，不生成）"},"POST");
      project=await request("/"+project.id+"/plan",{expected_revision:project.revision,locked_fields:[],plan:{
        brief:{title:proofTitle,audience:"普通观众",core_message:"展示直接生成流程"},
        persona:"科普资料讲解员",visual_style:"简洁图解",layout:"9:16竖屏",resolution:"720p",
        audio_plan:{narration_style:"清晰自然",voice:"默认AI声音",music:"无",sound_notes:"旁白清晰"},
        scenes:[{id:"direct-scene-1",title:"太阳的疑问",duration:6,narration:"太阳为什么能够持续发光？",visual_intent:"太阳的科学示意",seedance_prompt:"太阳科学示意，无文字"}],
        interest_hooks:[{id:"hook-direct",kind:"opening",scene_id:"direct-scene-1",copy:"太阳的能量从哪里来？",payoff_scene_id:"direct-scene-1"}]
      }},"PUT");
      return {project_id:project.id,cost_review_required:project.capabilities.cost_review_required,provider:project.capabilities.scene.provider,generation_submissions:0};
    },proofTitle);
    await page.reload();
    await page.getByRole("button",{name:"内容生成",exact:true}).click();
    await page.getByRole("tab",{name:"视频制作",exact:true}).click();
    await page.getByRole("button",{name:new RegExp(proofTitle)}).click();
    await expect(page.getByRole("region",{name:"本次费用授权"})).toHaveCount(0);
    // Historical project titles can mention prices; only generation actions are checked.
    await expect(page.getByRole("button",{name:/^(报价|核算|获取报价|确认费用)/})).toHaveCount(0);
    await expect(page.getByRole("button",{name:/生成配音试听/})).toBeVisible();
    if(quoteRequests.length)throw new Error("Direct-flow UI requested a quote");
    await page.locator(".video-editor-scroll").evaluate(element=>{element.scrollTop=0;});
    await page.screenshot({path:path.join(evidence,"electron-direct-1080.png")});
    await native.evaluate(w=>w.setSize(1440,920));
    await page.screenshot({path:path.join(evidence,"electron-direct-1440.png")});
    await writeFile(path.join(evidence,"direct-generation-proof.json"),JSON.stringify(directProof,null,2));
  }
  if(process.env.VIDEO_EXPECT_PRICING_PROFILE === "1") {
    if(state.pricing_profile !== "official-cn-20261022") throw new Error("Official pricing profile was not activated");
    const proofTitle=`${expectRunninghub ? "RunningHub报价验证" : "官方报价验证"}-${Date.now()}（不生成）`;
    pricingProof = await page.evaluate(async({expectRunninghub,proofTitle})=>{
      const session=await window.signalDesk.loadAuthSession();
      async function request(route,body,method="GET") {
        const response=await fetch(session.backendUrl+"/api/v1/video/projects"+route,{method,headers:{Authorization:"Bearer "+session.accessToken,"Content-Type":"application/json"},...(body?{body:JSON.stringify(body)}:{})});
        if(!response.ok) throw new Error("Pricing smoke metadata request failed: "+response.status);
        return response.json();
      }
      let project=await request("",{topic:"太阳为什么发光（官方报价验证，不生成）"},"POST");
      project=await request("/"+project.id+"/plan",{expected_revision:project.revision,locked_fields:[],plan:{
        brief:{title:proofTitle,audience:"普通观众",core_message:"展示真实官方计费配置"},
        persona:"科普资料讲解员",visual_style:"简洁图解",layout:"9:16竖屏",resolution:"720p",
        audio_plan:{narration_style:"清晰自然",voice:"默认AI声音",music:"无",sound_notes:"旁白清晰"},
        scenes:[{id:"price-scene-1",title:"太阳的疑问",duration:6,narration:"太阳为什么能够持续发光？",visual_intent:"太阳的科学示意",seedance_prompt:"太阳科学示意，无文字"}],
        interest_hooks:[{id:"hook-price",kind:"opening",scene_id:"price-scene-1",copy:"太阳的能量从哪里来？",payoff_scene_id:"price-scene-1"}]
      }},"PUT");
      const scene=await request("/"+project.id+"/quote?kind=scene&scene_id=price-scene-1");
      const voice=await request("/"+project.id+"/quote?kind=voice");
      if(voice.amount!==1||voice.pricing_kind!=="reservation")throw new Error("Voice quote arithmetic mismatch");
      if(expectRunninghub) {
        if(scene.provider!=="runninghub"||scene.pricing_source!=="runninghub_price_preview"||!["CNY","USD"].includes(scene.currency)||typeof scene.quote_fingerprint!=="string"||!Number.isFinite(scene.amount)||scene.amount<=0||scene.reservation_amount<scene.amount)throw new Error("RunningHub official live quote mismatch");
      } else if(scene.amount!==2.9808||scene.reservation_amount!==4.1732) throw new Error("Ark quote arithmetic mismatch");
      if(scene.provider_hard_cap!==false||voice.provider_hard_cap!==false)throw new Error("Reservation incorrectly claims a provider cap");
      return {project_id:project.id,scene,voice};
    },{expectRunninghub,proofTitle});
    await page.reload();
    await page.getByRole("button",{name:"内容生成",exact:true}).click();
    await page.getByRole("tab",{name:"视频制作",exact:true}).click();
    await page.getByRole("button",{name:new RegExp(proofTitle)}).click();
    await page.getByRole("button",{name:"单独核算配音试听",exact:true}).click();
    let budget=page.getByRole("region",{name:"本次费用授权"});
    await expect(budget.getByText("软件预算预留",{exact:true})).toBeVisible();
    await expect(budget.getByRole("link",{name:"查看官方计费说明"})).toHaveAttribute("href","https://help.aliyun.com/zh/model-studio/model-pricing");
    await budget.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidence,"electron-official-voice-1080.png")});
    await budget.getByRole("button",{name:"取消本次授权"}).click();
    await page.getByText("高级：模型提示词和项目配置",{exact:true}).click();
    await page.getByRole("button",{name:"核算当前镜头",exact:true}).click();
    budget=page.getByRole("region",{name:"本次费用授权"});
    const unit=pricingProof.scene.currency==="USD" ? "美元（USD）" : "元";
    await expect(budget.getByText(`${pricingProof.scene.amount} ${unit}`,{exact:true})).toBeVisible();
    await expect(budget.getByText(`${pricingProof.scene.reservation_amount} ${unit}`,{exact:true})).toBeVisible();
    if(expectRunninghub) {
      await expect(budget.getByText("RunningHub 实时报价",{exact:true})).toBeVisible();
      await expect(budget.getByRole("link",{name:"查看官方计费说明"})).toHaveAttribute("href","https://www.runninghub.cn/runninghub-api-doc-cn/api-454850620");
      await expect(budget.getByLabel("本次预算上限")).toHaveValue(String(pricingProof.scene.reservation_amount));
    }
    await budget.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidence,"electron-official-scene-1080.png")});
    await native.evaluate(w=>w.setSize(1440,920));
    await page.screenshot({path:path.join(evidence,"electron-official-scene-1440.png")});
    await budget.getByRole("button",{name:"取消本次授权"}).click();
    await writeFile(path.join(evidence,"official-pricing-proof.json"),JSON.stringify(pricingProof,null,2));
  }
  if(caps.planning.source === "hermes") {
    const sourceNote=page.getByText("策划已复用 Hermes 现有模型（"+caps.planning.model+"）。",{exact:true});
    await expect(sourceNote).toHaveCount(1);
    await sourceNote.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidence,"electron-hermes-model.png")});
  }
  if(errors.length) throw new Error(errors.join("\n"));
  await writeFile(path.join(evidence,"report.json"),JSON.stringify({status:"PASS",environment:"real-local-electron",provider_simulation:false,paid_calls:0,pricing_profile:state.pricing_profile,pricingProof,directProof,viewports:["1440x920 window","1080x760 window"],checks:["login and encrypted session","content/video navigation","default standard mode","capability response","paid adapters disabled","minimum width","no page errors"],capabilities:caps},null,2));
  console.log("PASS: actual Electron login, video entry, isolated capabilities and desktop layouts; no paid calls");
} catch(error) {
  if(page) {
    await page.screenshot({path:path.join(evidence,"failed.png")}).catch(()=>{});
    console.error("Desktop body:", (await page.locator("body").innerText()).slice(0,1600));
  }
  throw error;
} finally {
  if(app) await app.close();
  for(const child of children) if(child.exitCode===null) child.kill();
}
