"""Real installed SDK/plugin/API/MySQL; synthetic Weixin envelope, no messages/models.

Run over the authorized SSH connection after defining ACCOUNT_HELPER to the
reviewed knowledge-production-account.py source. No credentials are printed.
"""
import base64,hashlib,json,os,subprocess,sys
from pathlib import Path

os.chdir('/opt/signaldesk-task');sys.path.insert(0,os.getcwd())
from app.database import SessionLocal
from app.knowledge import storage_root
from app.knowledge_models import KnowledgeAsset,KnowledgeItem,KnowledgeNonce,KnowledgeCloudUsage
from sqlalchemy import select,delete,func

sample=base64.b64decode(PUBLIC_SAMPLE)
assert hashlib.sha256(sample).hexdigest()=='e267dac1cef824b188e5c8659a513bbe147dc140fbefdf90ec2fc92ea0c734f3'
r=subprocess.run([sys.executable,'-','create','--synthetic-weixin'],input=ACCOUNT_HELPER,capture_output=True,text=True,timeout=30)
assert r.returncode==0,'Synthetic identity creation failed'
login=json.loads(r.stdout);marker=login['marker'];nonces=[];result={'passed':False,'native_wechat_host':False,'event_envelope':'synthetic Weixin fixture','qq_tested':False,'external_message_sent':False,'cloud_calls':0}
try:
 code="""
import asyncio,base64,hashlib,importlib.util,json,os,tempfile
from pathlib import Path
from types import SimpleNamespace
from gateway.platforms.base import get_audio_cache_dir
spec=importlib.util.spec_from_file_location('kb063_installed','/opt/data/plugins/wechat-knowledge/__init__.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
generated=[];original=m.secrets.token_urlsafe
def record(n):
 value=original(n);generated.append(value);return value
m.secrets.token_urlsafe=record
recipient=RECIPIENT;sample=base64.b64decode(SAMPLE)
source=SimpleNamespace(platform='weixin',user_id=recipient,chat_type='dm')
def event(text,media=None):return SimpleNamespace(source=source,text=text,media_urls=media or [],media_types=['audio'] if media else [],message_id='kb063-'+original(20))
def invoke(e):
 rewrite=m.pre_gateway_dispatch(event=e,gateway=None);assert rewrite['action']=='rewrite'
 return asyncio.run(m.result_command(rewrite['text'].split(' ',1)[1]))
root=Path(get_audio_cache_dir()).resolve();assert root.is_dir()
fd,name=tempfile.mkstemp(prefix='kb063-synthetic-',suffix='.aud',dir=root);path=Path(name)
try:
 with os.fdopen(fd,'wb') as stream:stream.write(sample)
 before=hashlib.sha256(path.read_bytes()).hexdigest()
 enabled=invoke(event('我的知识库附件收藏：开启'));assert '已开启' in enabled
 attachment=event('',[str(path)]);reply=invoke(attachment);assert '原件已保存' in reply
 assert attachment.media_urls==[] and hashlib.sha256(path.read_bytes()).hexdigest()==before
 disabled=invoke(event('我的知识库附件收藏：关闭'));assert '已关闭' in disabled
 normal=event('普通消息');assert m.pre_gateway_dispatch(event=normal,gateway=None) is None
 print(json.dumps({'passed':True,'real_sdk_cache_getter':True,'installed_plugin_sha256':hashlib.sha256(Path('/opt/data/plugins/wechat-knowledge/__init__.py').read_bytes()).hexdigest(),'original_preserved':True,'only_attachment_event_consumed':True,'mode_enabled_and_disabled':True,'nonces':generated}))
finally:path.unlink(missing_ok=True)
"""
 code='RECIPIENT='+repr(login['synthetic_recipient'])+'\nSAMPLE='+repr(base64.b64encode(sample).decode())+'\n'+code
 r=subprocess.run(['docker','exec','-i','-e','HERMES_HOME=/opt/data','hermes','/opt/hermes/.venv/bin/python','-'],input=code,capture_output=True,text=True,timeout=150)
 assert r.returncode==0,'Installed plugin fixture failed'
 details=json.loads(r.stdout.strip().splitlines()[-1]);nonces=details.pop('nonces')
 identity=json.loads(Path(marker).read_text());uid=identity['user_id']
 with SessionLocal() as db:
  items=list(db.scalars(select(KnowledgeItem).where(KnowledgeItem.owner_id==uid)));assert len(items)==1
  assets=list(db.scalars(select(KnowledgeAsset).where(KnowledgeAsset.item_id==items[0].id)));assert len(assets)==1
  asset=assets[0];stored=storage_root()/asset.storage_name
  assert asset.sha256==hashlib.sha256(sample).hexdigest()==hashlib.sha256(stored.read_bytes()).hexdigest()
  assert db.scalar(select(func.count()).select_from(KnowledgeCloudUsage).where(KnowledgeCloudUsage.owner_id==uid))==0
 result.update(passed=True,actual_production_api=True,actual_mysql=True,stored_original_sha256=asset.sha256,checks=details)
except Exception as error:
 result.update(error_type=type(error).__name__,detail=str(error) if isinstance(error,AssertionError) else 'Test failed; no credentials logged')
finally:
 if nonces:
  with SessionLocal() as db:db.execute(delete(KnowledgeNonce).where(KnowledgeNonce.nonce.in_(nonces)));db.commit()
 clean=subprocess.run([sys.executable,'-','cleanup','--marker',marker],input=ACCOUNT_HELPER,capture_output=True,text=True,timeout=40)
 result['synthetic_identity_cleaned']=clean.returncode==0
 result['test_nonces_cleaned']=bool(nonces)
print(json.dumps(result));raise SystemExit(0 if result['passed'] and result['synthetic_identity_cleaned'] else 1)
