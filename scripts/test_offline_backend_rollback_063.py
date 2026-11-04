"""Actual local API/database/worker survives code/config upgrade and forced rollback.

No production services, files, accounts or network endpoints are accessed. This
checks Windows file attributes only; Linux UID/GID restoration is not claimed.
"""
import base64,hashlib,json,os,secrets,socket,stat,subprocess,sys,time,zipfile
from pathlib import Path
import httpx
ROOT=Path('D:/maiyi/tmp/kb-install-063-backend').resolve()
OUT=Path('D:/maiyi/docs/delivery/knowledge-remaining-acceptance-20261028/install/backend063').resolve();OUT.mkdir(parents=True,exist_ok=True)
BASE=Path('D:/maiyi/tmp/kb-remaining-src')
OLD=Path('D:/maiyi/docs/delivery/knowledge-complete-20261028/packages/SignalDesk-0.6.1-source.zip')
RUN=ROOT/('backend-run-'+secrets.token_hex(5));RUN.mkdir();ACTIVE=RUN/'active';ACTIVE.mkdir();BACKUP=RUN/'backup';BACKUP.mkdir()
assert ACTIVE.resolve().is_relative_to(ROOT)
with zipfile.ZipFile(OLD) as z:
 for name in z.namelist():
  if name.startswith('server/app/') and name.endswith('.py'):
   path=ACTIVE/name.removeprefix('server/');assert path.resolve().is_relative_to(ACTIVE);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(z.read(name))
data=RUN/'data';data.mkdir();files=data/'originals';files.mkdir()
env={'TASK_DATABASE_URL':'sqlite:///'+(data/'knowledge.db').as_posix(),'TASK_API_KEY':'synthetic-local-only','TASK_KNOWLEDGE_STORAGE_DIR':files.as_posix(),'TASK_VIDEO_STORAGE_DIR':(data/'video').as_posix(),'TASK_KNOWLEDGE_SILK_ENABLED':'false','TASK_KNOWLEDGE_ASR_ENABLED':'false','TASK_KNOWLEDGE_CLOUD_ASR_ENABLED':'false','TASK_KNOWLEDGE_OCR_ENABLED':'false','TASK_KNOWLEDGE_CLOUD_QA_ENABLED':'false','TASK_KNOWLEDGE_SEMANTIC_ENABLED':'false','TASK_KNOWLEDGE_ORGANIZATION_ENABLED':'false','TASK_KNOWLEDGE_DISPATCH_ENABLED':'false','TASK_KNOWLEDGE_NOTIFICATION_WEBHOOK_BASE':'','TASK_KNOWLEDGE_NOTIFICATION_WEBHOOK_SECRET':'','TASK_KNOWLEDGE_GATEWAY_SECRET':'','TASK_PROMPT_API_KEY':'','TASK_HERMES_WEBHOOK_URL':''}
env_path=ACTIVE/'.env';env_path.write_text('\n'.join(k+'='+v for k,v in env.items())+'\n',encoding='utf-8')
changed=['app/knowledge_gateway.py','app/knowledge.py','app/knowledge_asr.py','app/knowledge_extract.py','app/knowledge_silk.py','.env','requirements-silk.txt']
metadata={}
for name in changed:
 path=ACTIVE/name
 if path.exists():
  content=path.read_bytes();(BACKUP/name).parent.mkdir(parents=True,exist_ok=True);(BACKUP/name).write_bytes(content)
  metadata[name]={'sha256':hashlib.sha256(content).hexdigest(),'mode':stat.S_IMODE(path.stat().st_mode)}
 else:metadata[name]=None
(BACKUP/'file-metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
process=None;logs=[];phase=[];checks=[];token=None;other=None
base='http://127.0.0.1:'+str(port)
child_env={k:v for k,v in os.environ.items() if not k.startswith(('TASK_','PADDLE_','ARK_','DASHSCOPE_')) and k not in {'PYTHONPATH','PYTHONHOME'}}
child_env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
def start(name,expected=True):
 global process
 log=(OUT/(name+'.log')).open('w',encoding='utf-8');logs.append(log)
 process=subprocess.Popen([sys.executable,str(ROOT/'rollback_server.py'),'--root',str(ACTIVE),'--port',str(port)],cwd=ACTIVE,env=child_env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
 for _ in range(100):
  if process.poll() is not None:
   if expected:raise AssertionError(name+' server exited '+str(process.returncode))
   return False
  try:
   if httpx.get(base+'/health',timeout=.5,trust_env=False).status_code==200:
    assert expected,'Faulty code unexpectedly started';return True
  except httpx.HTTPError:pass
  time.sleep(.1)
 raise AssertionError('Service readiness timeout '+name)
def stop():
 global process
 if process and process.poll() is None:process.terminate();process.wait(timeout=15)
 process=None
def api(method,path,**kwargs):
 headers=kwargs.pop('headers',{});headers={**({'Authorization':'Bearer '+token} if token else {}),**headers}
 r=httpx.request(method,base+'/api/v1'+path,headers=headers,timeout=20,trust_env=False,follow_redirects=False,**kwargs)
 assert r.status_code<400,(method,path,r.status_code,r.text[:180]);return r.json() if r.status_code!=204 else None
def state():
 r=httpx.get(base+'/acceptance-info',timeout=5,trust_env=False).json();assert r['schema']=='20261008_12';return r
def citation_and_original(item_id,asset_id,expected):
 detail=api('GET','/knowledge/items/'+item_id);assert detail['content']==expected
 answer=api('POST','/knowledge/ask',json={'question':'回退验收资料'})
 assert any(c['item_id']==item_id for c in answer['citations'])
 r=httpx.get(base+'/api/v1/knowledge/assets/'+asset_id,headers={'Authorization':'Bearer '+token},timeout=10,trust_env=False);assert r.status_code==200 and r.content==original
 unauthorized=httpx.get(base+'/api/v1/knowledge/items/'+item_id,headers={'Authorization':'Bearer '+other},timeout=10,trust_env=False);assert unauthorized.status_code==404
 return detail
candidate_hashes={name:hashlib.sha256((BASE/'server'/name).read_bytes()).hexdigest() for name in changed if name!='.env' and (BASE/'server'/name).is_file()}
result={'candidate_version':'0.6.3','candidate_code_sha256':candidate_hashes,'passed':False,'scope':'isolated Windows loopback API + SQLite + real local worker; code/config forced rollback','production_changed':False,'real_nsis_executed':False,'qq_sent':False,'linux_uid_gid_tested':False,'workspace':str(RUN)}
try:
 start('backend-before-061');assert state()['silk_enabled'] is False
 token=api('POST','/auth/login',json={'username':'rollback-test','password':'synthetic-only-password'})['access_token']
 other=api('POST','/auth/login',json={'username':'rollback-other','password':'synthetic-only-password'})['access_token']
 note=api('POST','/knowledge/items',json={'title':'回退验收资料','content':'回退验收资料原文：保留原始来源与附件。'})
 original='回退验收资料原件；保留原件和来源。'.encode()
 upload=api('POST','/knowledge/capture/files',content=original,headers={'Content-Type':'application/octet-stream','X-Content-SHA256':hashlib.sha256(original).hexdigest(),'X-Filename-Base64':base64.b64encode('回退验收资料原件.txt'.encode()).decode(),'Idempotency-Key':secrets.token_hex(32)})
 file_id=upload['item']['id']
 for _ in range(100):
  uploaded=api('GET','/knowledge/items/'+file_id)
  if uploaded['status']=='ready':break
  time.sleep(.1)
 assert uploaded['status']=='ready';asset=uploaded['assets'][0]['id']
 citation_and_original(file_id,asset,uploaded['content']);checks+=['0.6.1 actual API creates note and original; real worker parses and citation resolves','owner session and other-owner rejection established']
 stop()
 for name in changed:
  if name=='.env':continue
  source=BASE/'server'/name
  if source.exists():(ACTIVE/name).write_bytes(source.read_bytes())
 env_path.write_text(env_path.read_text(encoding='utf-8').replace('TASK_KNOWLEDGE_SILK_ENABLED=false','TASK_KNOWLEDGE_SILK_ENABLED=true'),encoding='utf-8')
 start('backend-upgraded-063');assert state()['silk_enabled'] is True;assert api('GET','/auth/me')['username']=='rollback-test'
 citation_and_original(file_id,asset,uploaded['content'])
 revised=api('PATCH','/knowledge/items/'+note['id'],json={'revision':note['revision'],'title':note['title'],'content':'回退验收资料升级后修订：新数据必须在回退后保留。'})
 checks+=['0.6.3 retains existing session, source bytes, private access and citations','0.6.3 writes a new revision without schema change']
 stop()
 (ACTIVE/'app/knowledge.py').write_text('raise SyntaxError("Synthetic failed deployment")\n',encoding='utf-8')
 assert start('backend-injected-startup-failure',expected=False) is False;stop()
 assert 'Synthetic failed deployment' in (OUT/'backend-injected-startup-failure.log').read_text(encoding='utf-8')
 checks.append('forced faulty-code startup fails for the injected SyntaxError before rollback')
 for name,meta in metadata.items():
  target=ACTIVE/name;assert target.resolve().is_relative_to(ACTIVE.resolve())
  if meta:
   target.write_bytes((BACKUP/name).read_bytes());target.chmod(meta['mode'])
  elif target.exists():target.unlink()
 for name,meta in metadata.items():
  target=ACTIVE/name
  if meta:assert hashlib.sha256(target.read_bytes()).hexdigest()==meta['sha256'] and stat.S_IMODE(target.stat().st_mode)==meta['mode']
  else:assert not target.exists()
 start('backend-restored-061');assert state()['silk_enabled'] is False;assert api('GET','/auth/me')['username']=='rollback-test'
 assert api('GET','/knowledge/items/'+note['id'])['content']==revised['content']
 citation_and_original(file_id,asset,uploaded['content'])
 assert len(api('GET','/knowledge/items')['items'])==2
 checks+=['0.6.1 code and .env restored to exact original hashes and Windows file attributes','new 0.6.3 revision, existing session, original, citations and owner isolation survive rollback','optional dependency declaration removed on rollback; no database snapshot overwritten']
 result.update(passed=True,checks=checks,old_version='0.6.1',upgraded_version='0.6.3',restored_version='0.6.1',schema='20261008_12',original_sha256=hashlib.sha256(original).hexdigest(),code_env_metadata_restored=True,business_data_retained=True,cloud_requests=0)
except Exception as error:
 result.update(error_type=type(error).__name__,detail=str(error)[:400]);raise
finally:
 stop()
 for log in logs:log.close()
 (OUT/'offline-backend-rollback.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'passed':result['passed'],'checks':len(checks),'production_changed':False,'real_nsis_executed':False}))
