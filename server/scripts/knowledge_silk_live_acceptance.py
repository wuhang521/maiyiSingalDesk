"""One public voice fixture through the actual loopback API/MySQL/worker/Qwen.

Creates one temporary test account without any notification channel. Cleans only
that account's test data, and never queries or exports existing user identities.
"""
import argparse,base64,hashlib,json,os,re,secrets,sys,time
from pathlib import Path
os.chdir('/opt/signaldesk-task');sys.path.insert(0,os.getcwd());os.umask(0o077)
import httpx
from sqlalchemy import delete,or_,select
from app.database import Base,SessionLocal
from app import knowledge_models as k
from app.models import Staff,StaffChannel,UserAccount,UserSession,utcnow
from app.auth import hash_password
from app.knowledge_service import purge_item
p=argparse.ArgumentParser();p.add_argument('--sample',type=Path,required=True);a=p.parse_args()
data=a.sample.read_bytes();digest=hashlib.sha256(data).hexdigest()
assert digest=='e267dac1cef824b188e5c8659a513bbe147dc140fbefdf90ec2fc92ea0c734f3'
username='kb-silk-check-'+secrets.token_hex(6);password=secrets.token_urlsafe(32)
with SessionLocal() as db:
 staff=Staff(name='SILK release acceptance');db.add(staff);db.flush()
 user=UserAccount(username=username,staff_id=staff.id,role='member',password_hash=hash_password(password));db.add(user);db.commit();uid=user.id;sid=staff.id
marker=Path('/var/tmp')/('signaldesk-silk-live-'+username+'.json');marker.write_text(json.dumps({'uid':uid,'sid':sid,'username':username}));marker.chmod(0o600)
result={'passed':False,'source_sha256':digest,'qq_sent':False,'notification_channel_created':False,'actual_api':True,'actual_mysql':True,'actual_worker':True,'actual_qwen':True}
try:
 with httpx.Client(base_url='http://127.0.0.1:19080',timeout=30,trust_env=False,follow_redirects=False) as client:
  def request(method,path,**kwargs):
   r=client.request(method,'/api/v1'+path,**kwargs)
   if r.status_code>=400:raise RuntimeError('API '+path.split('?')[0]+' returned '+str(r.status_code))
   return r.json() if r.status_code!=204 else None
  login=request('POST','/auth/login',json={'username':username,'password':password});client.headers['Authorization']='Bearer '+login['access_token']
  caps=request('GET','/knowledge/capabilities');assert caps['silk_available'] and caps['cloud_asr_enabled'] and '千问' in caps['cloud_asr_provider']
  captured=request('POST','/knowledge/capture/files',content=data,headers={'Content-Type':'application/octet-stream','X-Content-SHA256':digest,'X-Filename-Base64':base64.b64encode('公开Tencent语音验收.amr'.encode()).decode(),'Idempotency-Key':hashlib.sha256(username.encode()).hexdigest()})
  itemid=captured['item']['id'];path='/knowledge/items/'+itemid
  for _ in range(45):
   item=request('GET',path)
   if item['status'] not in {'received','extracting','retry_wait'}:break
   time.sleep(1)
  assert item['status']=='needs_input'
  duplicate=request('POST','/knowledge/capture/files',content=data,headers={'Content-Type':'application/octet-stream','X-Content-SHA256':digest,'X-Filename-Base64':base64.b64encode('公开Tencent语音验收.amr'.encode()).decode(),'Idempotency-Key':hashlib.sha256(username.encode()).hexdigest()})
  assert duplicate['item']['id']==itemid
  asset=item['assets'][0];original=request('GET',path)
  queued=request('POST',path+'/assets/'+asset['id']+'/asr',json={'revision':item['revision'],'cloud_consent':True})
  for _ in range(100):
   item=request('GET',path)
   if item['status'] not in {'received','extracting','retry_wait'}:break
   time.sleep(1)
  if item['status']!='needs_review':raise RuntimeError('ASR finished in '+item['status']+': '+item.get('status_note',''))
  assert item['method']=='qwen_asr_draft' and item['content'].strip()
  before=request('POST','/knowledge/ask',json={'question':'公开Tencent语音验收'})
  assert not before['citations']
  approved=request('POST',path+'/asr/confirm',json={'revision':item['revision'],'confirmation':'已核对'})
  assert approved['method']=='qwen_asr_reviewed'
  after=request('POST','/knowledge/ask',json={'question':'公开Tencent语音验收'})
  assert after['citations'] and any(x.get('item_id')==itemid for x in after['citations'])
  # Compare server-stored original bytes using this temporary account's asset only.
  with SessionLocal() as db:
   record=db.get(k.KnowledgeAsset,asset['id']);from app.knowledge import storage_root
   original_bytes=(storage_root()/record.storage_name).read_bytes();assert hashlib.sha256(original_bytes).hexdigest()==digest
  result.update(passed=True,transcript=item['content'],method=approved['method'],citation_locator=after['citations'][0]['locator'],original_preserved=True,deduplicated=True,draft_excluded_before_confirmation=True,paid_submissions=request('GET','/knowledge/usage')['daily_submissions'])
except Exception as error:
 result['error_type']=type(error).__name__;result['detail']=str(error)[:260] if isinstance(error,(RuntimeError,AssertionError)) else 'Acceptance did not finish'
finally:
 (Path('/var/tmp')/('signaldesk-silk-result-'+username+'.json')).write_text(json.dumps(result,ensure_ascii=False))
 with SessionLocal() as db:
  user=db.get(UserAccount,uid);assert user and user.username==username and user.username.startswith('kb-silk-check-') and user.staff_id==sid
  ids=list(db.scalars(select(k.KnowledgeItem.id).where(k.KnowledgeItem.owner_id==uid)))
  for itemid in ids:
   item=db.get(k.KnowledgeItem,itemid);item.deleted_at=utcnow();db.commit();purge_item(db,uid,itemid,item.revision)
  usageids=select(k.KnowledgeCloudUsage.id).where(k.KnowledgeCloudUsage.owner_id==uid)
  db.execute(delete(k.KnowledgeProviderTrace).where(k.KnowledgeProviderTrace.usage_id.in_(usageids)))
  db.execute(delete(k.KnowledgeCostReservation).where(k.KnowledgeCostReservation.usage_id.in_(usageids)))
  for table in reversed(Base.metadata.sorted_tables):
   if not table.name.startswith('knowledge_'):continue
   conditions=[]
   if 'owner_id' in table.c:conditions.append(table.c.owner_id==uid)
   if ids and 'item_id' in table.c:conditions.append(table.c.item_id.in_(ids))
   if table.name=='knowledge_team_member':conditions.append(table.c.user_id==uid)
   if conditions:db.execute(delete(table).where(or_(*conditions)))
  db.execute(delete(UserSession).where(UserSession.user_id==uid));db.execute(delete(UserAccount).where(UserAccount.id==uid));db.execute(delete(Staff).where(Staff.id==sid));db.commit()
  result['test_account_cleaned']=db.get(UserAccount,uid) is None
 marker.unlink()
 print(json.dumps(result,ensure_ascii=False))
raise SystemExit(0 if result['passed'] and result['test_account_cleaned'] else 1)
