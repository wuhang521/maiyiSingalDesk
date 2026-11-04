"""Run on a NEW empty signaldesk_kb_test_* schema, never a production schema.

Uses installed SQLAlchemy/httpx/Uvicorn only; pytest is not required. Both the
HTTP API and MySQL are real. The signed WeChat envelope is synthetic, no external
chat/QQ/private identity or cloud model is contacted. Credentials stay in env.
"""
import argparse,base64,hashlib,hmac,json,os,secrets,socket,sys,tempfile,threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--source-root',type=Path,required=True)
p.add_argument('--sample',type=Path,required=True)
p.add_argument('--output',type=Path)
p.add_argument('--local-sqlite',action='store_true',help='Self-test this runner on a NEW temporary SQLite file; does not establish MySQL acceptance')
a=p.parse_args()
result={'passed':False,'actual_http_api':True,'external_wechat_host':False,'external_envelope':'synthetic signed fixture','qq_tested':False,'cloud_calls':0,'production_touched':False}
server=None;thread=None;engine=None;schema_created=False;work=None
try:
 from sqlalchemy import create_engine,inspect,select,text
 from sqlalchemy.engine import make_url
 from sqlalchemy.orm import sessionmaker
 work=tempfile.TemporaryDirectory(prefix='kb-ingress-mysql-')
 url=('sqlite:///'+(Path(work.name)/'runner.db').as_posix()) if a.local_sqlite else os.environ.get('KNOWLEDGE_TEST_DATABASE_URL','');parsed=make_url(url)
 assert a.local_sqlite or (parsed.get_backend_name()=='mysql' and parsed.database and parsed.database.startswith('signaldesk_kb_test_')),'A dedicated prefixed MySQL test schema is required'
 source=a.source_root.resolve();assert (source/'server/app/main.py').is_file() and not (source/'server/.env').exists(),'Use a staged source tree without production .env'
 data=a.sample.read_bytes();digest=hashlib.sha256(data).hexdigest()
 assert digest=='e267dac1cef824b188e5c8659a513bbe147dc140fbefdf90ec2fc92ea0c734f3','Unexpected public fixture'
 os.environ.update(TASK_DATABASE_URL=url,TASK_KNOWLEDGE_STORAGE_DIR=str(Path(work.name)/'originals'),
  TASK_KNOWLEDGE_GATEWAY_SECRET='synthetic-'+secrets.token_hex(32),TASK_KNOWLEDGE_CLOUD_ASR_ENABLED='false',
  TASK_KNOWLEDGE_OCR_ENABLED='false',TASK_KNOWLEDGE_DISPATCH_ENABLED='false',TASK_KNOWLEDGE_CLOUD_QA_ENABLED='false',
  TASK_KNOWLEDGE_ASR_ENABLED='false',TASK_KNOWLEDGE_MAX_UPLOAD_MB='1')
 os.chdir(source/'server');sys.path.insert(0,str(source/'server'))
 from app.main import app
 from app.database import Base,get_db
 from app.models import Staff,StaffChannel,UserAccount
 from app.auth import create_user_session
 from app.knowledge_models import KnowledgeAsset,KnowledgeIngressReceipt,KnowledgeCloudUsage
 from app.config import get_settings
 import httpx,uvicorn
 engine=create_engine(url,pool_pre_ping=True,connect_args={'check_same_thread':False,'timeout':30} if a.local_sqlite else {})
 assert not inspect(engine).get_table_names(),'Refusing to modify a nonempty test schema'
 Base.metadata.create_all(engine);schema_created=True
 factory=sessionmaker(bind=engine,expire_on_commit=False,autoflush=False);users=[]
 tag=secrets.token_hex(8);senders=[f'ingress-{tag}-{i}' for i in range(3)]
 with factory() as db:
  version=db.execute(text('SELECT sqlite_version()' if a.local_sqlite else 'SELECT VERSION()')).scalar()
  for i in range(3):
   staff=Staff(name=f'Synthetic ingress {i}');staff.channels=[StaffChannel(platform='weixin',recipient_id=senders[i])]
   user=UserAccount(username=f'ingress-{tag}-{i}',staff=staff,role='admin' if i==2 else 'member',password_hash='unused')
   db.add(user);db.flush();token,_=create_user_session(db,user);users.append({'Authorization':'Bearer '+token})
  db.commit()
 def sessions():
  with factory() as db:yield db
 app.dependency_overrides[get_db]=sessions
 sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='critical',access_log=False,lifespan='off'))
 thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
 end=time.monotonic()+10
 while not server.started and time.monotonic()<end:time.sleep(.02)
 assert server.started,'Local API not ready';base=f'http://127.0.0.1:{port}/api/v1/knowledge'
 def headers(*,sender,message,nonce=None):
  metadata={'platform':'weixin','sender_id':sender,'chat_type':'dm','message_id':message,'filename':'public-fixture.amr'}
  encoded=base64.b64encode(json.dumps(metadata,separators=(',',':')).encode()).decode()
  stamp=str(int(time.time()));nonce=nonce or secrets.token_urlsafe(24)
  signature=hmac.new(get_settings().knowledge_gateway_secret.encode(),f'{stamp}\n{nonce}\n{encoded}\n{digest}'.encode(),hashlib.sha256).hexdigest()
  return {'Content-Type':'application/octet-stream','X-Knowledge-Time':stamp,'X-Knowledge-Nonce':nonce,'X-Knowledge-Signature':signature,'X-Knowledge-Metadata':encoded,'X-Knowledge-Content-SHA256':digest}
 def upload(h):
  r=httpx.post(base+'/gateway/attachment',headers=h,content=data,trust_env=False,timeout=30)
  assert r.headers['content-type'].startswith('application/json'),'Unexpected API response type'
  return r
 serial=headers(sender=senders[0],message='serial')
 first=upload(serial);assert first.status_code==200,f'Initial signature: {first.status_code}'
 item=first.json()['item'];replay=upload(serial);assert replay.status_code==409,f'Serial nonce replay: {replay.status_code}'
 # Ensure the route does not rely on one particular autoflush setting.
 factory.configure(autoflush=True)
 auto=headers(sender=senders[0],message='autoflush')
 assert upload(auto).status_code==200
 assert upload(auto).status_code==409
 factory.configure(autoflush=False)
 batches=[]
 for n in range(3):
  shared=headers(sender=senders[0],message=f'parallel-{n}')
  with ThreadPoolExecutor(max_workers=4) as pool:codes=list(pool.map(lambda _:upload(shared).status_code,range(4)))
  assert sorted(codes)==[200,409,409,409],f'Concurrent nonce statuses: {codes}'
  batches.append(codes)
 same=headers(sender=senders[1],message='serial');other=upload(same)
 assert other.status_code==200 and other.json()['item']['id']!=item['id']
 owner=httpx.get(base+'/items/'+item['id'],headers=users[0],trust_env=False)
 assert owner.status_code==200
 for index in [1,2]:
  r=httpx.get(base+'/items/'+item['id'],headers=users[index],trust_env=False)
  assert r.status_code==404,f'Cross-owner access: {r.status_code}'
 unbound=upload(headers(sender='unbound-'+tag,message='unbound'));assert unbound.status_code==403
 with factory() as db:
  assets=list(db.scalars(select(KnowledgeAsset)));assert len(assets)==6
  assert all(x.sha256==digest and x.size==len(data) for x in assets)
  for asset in assets:assert hashlib.sha256((Path(get_settings().knowledge_storage_dir)/asset.storage_name).read_bytes()).hexdigest()==digest
  assert len(list(db.scalars(select(KnowledgeIngressReceipt))))==6
  assert db.scalar(select(KnowledgeCloudUsage.id)) is None
 result.update(passed=True,database_backend=parsed.get_backend_name(),database_version=version,actual_mysql=not a.local_sqlite,serial_replay=409,autoflush_true_replay=409,concurrent_status_batches=batches,owner_isolation=True,stored_assets=6,public_fixture_sha256=digest)
except Exception as error:
 result.update(error_type=type(error).__name__,detail=str(error)[:220] if isinstance(error,AssertionError) else 'Test did not finish; no credentials exported')
finally:
 if server:server.should_exit=True
 if thread:thread.join(timeout=10)
 if engine and schema_created:
  try:
   assert not thread or not thread.is_alive(),'Server did not stop'
   assert set(inspect(engine).get_table_names())==set(Base.metadata.tables),'Unexpected test tables; cleanup refused'
   Base.metadata.drop_all(engine);result['test_schema_cleaned']=not inspect(engine).get_table_names()
  except Exception:
   result['test_schema_cleaned']=False;result['passed']=False
 if engine:engine.dispose()
 if work:work.cleanup()
 if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(result,ensure_ascii=True))
raise SystemExit(0 if result['passed'] and result.get('test_schema_cleaned') else 1)
