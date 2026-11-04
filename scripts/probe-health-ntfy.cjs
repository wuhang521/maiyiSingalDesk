// Network-only Electron diagnostic. No BrowserWindow and no real user profile.
const {app,net,session}=require('electron');
const fs=require('node:fs/promises');const path=require('node:path');
const [server,topic,profile,output]=process.argv.slice(2);
if(!server||!topic||!profile||!output)throw new Error('Expected server/topic/isolated-profile/output');
app.setPath('userData',path.resolve(profile));
app.whenReady().then(async()=>{
 const result={profile_isolated:true,network_only:true,configuration_changed:false};
 const origin=new URL(server);if(origin.protocol!=='https:')throw new Error('HTTPS is required');
 try{
  const proxy=await session.defaultSession.resolveProxy(server);
  result.proxy_kind=proxy.split(/[ ;]/)[0];
  const started=Date.now();const response=await net.fetch(server.replace(/\/$/,'')+'/'+encodeURIComponent(topic)+'/json?poll=1&since=5m',{signal:AbortSignal.timeout(15000)});
  result.http_status=response.status;result.elapsed_ms=Date.now()-started;result.content_type=response.headers.get('content-type');
  // Do not retain message data; only verify a finite response can be read.
  const text=await response.text();result.response_lines=text.split('\n').filter(Boolean).length;result.passed=response.ok;
 }catch(error){result.passed=false;result.error_type=error.name;result.error_code=error.message.match(/net::[A-Z_]+/)?.[0]||'request_failed';}
 await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify(result,null,2));console.log(JSON.stringify(result));app.exit(result.passed?0:1);
}).catch(()=>app.exit(1));
