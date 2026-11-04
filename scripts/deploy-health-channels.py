"""Apply reviewed health-only code and reversible data merge on the managed host.

Private backups and credentials stay on this host. Timer activation and visible
test deliveries are separate, verified steps after this transaction succeeds.
"""
import argparse,ast,errno,hashlib,json,os,pwd,re,stat,subprocess,sys,tarfile,tempfile,time,uuid
from datetime import date,datetime,timezone
from pathlib import Path
from urllib.request import urlopen

p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--sha256',required=True);p.add_argument('--start-date',required=True);a=p.parse_args()
assert date.fromisoformat(a.start_date).isoformat()==a.start_date
assert a.archive.parent==Path('/var/tmp') and a.archive.name.startswith('signaldesk-health-publish-') and not a.archive.is_symlink()
def digest(data):return hashlib.sha256(data).hexdigest()
assert digest(a.archive.read_bytes())==a.sha256

def require_manager_timezone():
 # systemd 219 has no `timedatectl show --value` and calendar expressions use
 # the manager's local zone when the timezone suffix is omitted for compatibility.
 environment={**os.environ,'LC_ALL':'C','LANG':'C'}
 reply=subprocess.run(['timedatectl','status'],capture_output=True,text=True,timeout=15,env=environment)
 zones=re.findall(r'^\s*Time zone:\s+([A-Za-z0-9_+/-]+)(?:\s|$)',reply.stdout,flags=re.MULTILINE)
 synchronized=re.findall(r'^\s*NTP synchronized:\s+(yes|no)(?:\s|$)',reply.stdout,flags=re.MULTILINE)
 if reply.returncode or zones!=['Asia/Shanghai'] or synchronized!=['yes']:
  raise RuntimeError('Server manager timezone must be Asia/Shanghai with NTP synchronized before deployment')
 return zones[0]

manager_timezone=require_manager_timezone()
root=Path('/opt/signaldesk-task');collector=Path('/opt/signaldesk');legacy=collector/'src/main.py'
targets={name:root/name for name in ['app/articles.py','app/schemas.py','app/channel_aliases.py','app/channel_reconcile.py','app/health_delivery.py','app/health_collector.py','app/health_relay.py']}
for kind in ['prepare','deliver']:
 for extension in ['service','timer']:
  name=f'signaldesk-health-{kind}.{extension}';targets['deploy/'+name]=Path('/etc/systemd/system')/name
old={'app/articles.py':'7682df73d804c1afb57983bdc1982bf5ab751ef2b817804cb2bd435203a9cdeb','app/schemas.py':'c96593faa5da4f34a6a9eef1cb0a1a4523468044fdef74548871b9882692ddd6'}
payload={}
with tarfile.open(a.archive) as tar:
 for member in tar:
  assert member.isfile() and member.name in targets and member.name not in payload
  payload[member.name]=tar.extractfile(member).read()
  if member.name.endswith('.py'):compile(payload[member.name],member.name,'exec')
assert set(payload)==set(targets)
for name,target in targets.items():
 assert not target.is_symlink()
 if name in old:assert target.is_file() and digest(target.read_bytes())==old[name],'Live task source differs'
 else:assert not target.exists(),'New health path already exists'
assert digest(legacy.read_bytes())=='ed41042405338eabca5a39f5ce38698a882835c43fa01a95f32fff9ac6b2c923','Live collector changed'

# Read the collector's configuration inside its existing runtime. Nothing is
# printed or exported; only the two verified target values enter our private file.
probe="import json;from pathlib import Path;from src.main import load_config;c=load_config(Path('/opt/signaldesk/.env'));h=next(r for r in c.recipients if r.key=='health');print(json.dumps({'server':c.ntfy_server,'topic':h.topic}))"
r=subprocess.run([str(collector/'.venv/bin/python'),'-c',probe],cwd=collector,capture_output=True,text=True,timeout=20)
assert r.returncode==0,'Publisher configuration unavailable';destination=json.loads(r.stdout)
assert destination['topic'] and destination['server'].startswith(('https://','http://'))
assert not any(c in destination['topic']+destination['server'] for c in '\r\n\x00')
account=pwd.getpwnam('signaldesk-task');uid,gid=account.pw_uid,account.pw_gid
os.umask(0o077);backup=Path('/var/backups/signaldesk-task')/('health-push-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:6]);backup.mkdir(mode=0o700)
saved={};meta={}
for name,target in {**targets,'collector-main.py':legacy}.items():
 if target.exists():
  info=target.stat();data=target.read_bytes();saved[name]=data;meta[name]={'uid':info.st_uid,'gid':info.st_gid,'mode':info.st_mode&511,'sha256':digest(data)}
  b=backup/'code'/name;b.parent.mkdir(parents=True,exist_ok=True);b.write_bytes(data);b.chmod(0o600)
 else:saved[name]=None;meta[name]=None
(backup/'code-metadata.json').write_text(json.dumps(meta,indent=2))
def atomic(path,data,owner,group,mode):
 fd,name=tempfile.mkstemp(prefix='.'+path.name+'.health-',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
  os.chown(name,owner,group);os.chmod(name,mode);os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)
def system(*parts):return subprocess.run(['systemctl',*parts],check=True,capture_output=True,timeout=50)
def healthy():
 for _ in range(50):
  try:
   with urlopen('http://127.0.0.1:19080/health',timeout=2) as response:
    if response.status==200:return True
  except OSError:pass
  time.sleep(.3)
 return False

def path_identity(path):
 info=path.lstat()
 return {'uid':info.st_uid,'gid':info.st_gid,'device':info.st_dev,'inode':info.st_ino,'mode':stat.S_IMODE(info.st_mode)}

def cleanup_private_paths(config,config_identity,state,state_identity):
 """Remove only exact files created here; never recurse into state or follow links."""
 outcome={'config':'not_created','state':'not_created'};errors=[]
 if config_identity is not None:
  try:
   if not config.exists() and not config.is_symlink():outcome['config']='already_absent'
   elif config.is_symlink() or not config.is_file():outcome['config']='retained_identity_changed'
   else:
    current=path_identity(config)
    if all(current[key]==value for key,value in config_identity.items() if key!='sha256') and digest(config.read_bytes())==config_identity['sha256']:
     config.unlink();outcome['config']='removed_exact_created_file'
    else:outcome['config']='retained_identity_or_content_changed'
  except Exception as error:
   outcome['config']='retained_cleanup_error';errors.append({'stage':'cleanup_created_config','error_type':type(error).__name__})
 if state_identity is not None:
  try:
   if not state.exists() and not state.is_symlink():outcome['state']='already_absent'
   elif state.is_symlink() or not state.is_dir() or path_identity(state)!=state_identity:outcome['state']='retained_identity_changed'
   else:
    try:state.rmdir();outcome['state']='removed_exact_empty_created_directory'
    except OSError as error:
     if error.errno in {errno.ENOTEMPTY,errno.EEXIST}:outcome['state']='retained_new_state_nonempty'
     else:raise
  except Exception as error:
   outcome['state']='retained_cleanup_error';errors.append({'stage':'cleanup_created_state','error_type':type(error).__name__})
 return outcome,errors

def ensure_api_started():
 errors=[]
 for stage,parts in [('daemon_reload',('daemon-reload',)),('start',('start','signaldesk-task-api'))]:
  try:system(*parts)
  except Exception as error:errors.append({'stage':'api_restart_'+stage,'error_type':type(error).__name__})
 try:available=healthy()
 except Exception as error:
  available=False;errors.append({'stage':'api_restart_health','error_type':type(error).__name__})
 return available,errors

before=None;merge=None;config=Path('/etc/signaldesk/health-delivery.env');state=Path('/var/lib/signaldesk-health')
assert not config.exists() and not config.is_symlink() and not state.exists() and not state.is_symlink(),'Health state/config path already exists'
config_identity=None;config_parent_identity=None;state_identity=None;state_created=False;rollback_errors=[];result=None
try:
 system('stop','signaldesk-task-api')
 for name,target in targets.items():
  owner,group,mode=(uid,gid,0o640) if name.startswith('app/') else (0,0,0o644)
  atomic(target,payload[name],owner,group,mode)
 os.chdir(root);sys.path.insert(0,str(root))
 from app.database import SessionLocal,engine
 from app.models import SmartChannel
 from app.channel_aliases import SmartChannelAlias
 from app.channel_reconcile import capture,fingerprints,reconcile,restore,digest as object_digest
 SmartChannelAlias.__table__.create(engine,checkfirst=True)
 with SessionLocal() as db:
  old_channel=db.get(SmartChannel,'demo-health')
  assert old_channel and old_channel.topic.strip()==destination['topic'].strip() and old_channel.server.rstrip('/')==destination['server'].rstrip('/'),'Publisher does not match verified legacy channel'
  before=capture(db);data=json.dumps(before,ensure_ascii=False,sort_keys=True).encode();(backup/'channel-before.json').write_bytes(data);(backup/'channel-before.json').chmod(0o600)
  merge=reconcile(db,destination['topic'],destination['server']);db.commit()
 with SessionLocal() as db:
  after=capture(db);assert fingerprints(before)==fingerprints(after) and object_digest(after)==merge['after_digest'];limit=db.get(SmartChannel,'health').max_items

 # Add exactly one guarded continue in the legacy health plan loop. Its source
 # remains on this host; removing the inserted AST node reproduces the old AST.
 original=saved['collector-main.py'].decode();tree=ast.parse(original)
 function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_all_recipients')
 loops=[n for n in ast.walk(function) if isinstance(n,ast.For) and ast.unparse(n.target)=='plan' and ast.unparse(n.iter)=='plans'];assert len(loops)==1
 loop=loops[0];lines=original.splitlines(keepends=True);line=lines[loop.body[0].lineno-1];indent=line[:len(line)-len(line.lstrip())]
 guard=[indent+'# SIGNALDESK_HEALTH_MANAGED_V1: separate prepared delivery owns health.\n',indent+'if should_send and __import__("os").environ.get("SIGNALDESK_HEALTH_MANAGED") == "1" and plan.recipient.key == "health":\n',indent+'    continue\n']
 changed=''.join(lines[:loop.body[0].lineno-1]+guard+lines[loop.body[0].lineno-1:]);new_tree=ast.parse(changed)
 f=next(n for n in new_tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_all_recipients');new_loop=next(n for n in ast.walk(f) if isinstance(n,ast.For) and ast.unparse(n.target)=='plan' and ast.unparse(n.iter)=='plans');assert isinstance(new_loop.body[0],ast.If);new_loop.body.pop(0)
 assert ast.dump(tree)==ast.dump(new_tree),'Legacy patch changes more than the guard';compile(changed,str(legacy),'exec')
 m=meta['collector-main.py'];atomic(legacy,changed.encode(),m['uid'],m['gid'],m['mode'])

 if not config.parent.exists():
  config.parent.mkdir(mode=0o750);config_parent_identity=path_identity(config.parent)
  # The process umask is 077: mkdir(mode=0750) alone becomes 0700. Only this
  # newly created parent may be widened for the service group's traversal.
  config.parent.chmod(0o750);os.chown(config.parent,0,gid)
  config_parent_identity=path_identity(config.parent)
 text='NTFY_SERVER='+destination['server'].rstrip('/')+'\nHEALTH_NTFY_TOPIC='+destination['topic'].strip()+'\nHEALTH_MAX_ITEMS='+str(min(100,max(1,limit)))+'\nHEALTH_START_DATE='+a.start_date+'\nCOLLECTOR_PYTHON=/opt/signaldesk/.venv/bin/python\n'
 # The absent-path preflight and planned hash/owner also identify a completed
 # atomic write if a later stat fails. Never delete a changed or unrelated file.
 config_identity={'uid':uid,'gid':gid,'mode':0o600,'sha256':digest(text.encode())}
 atomic(config,text.encode(),uid,gid,0o600);config_identity.update(path_identity(config))
 state.mkdir(mode=0o700);state_created=True;state_identity=path_identity(state)
 os.chown(state,uid,gid);state_identity=path_identity(state)
 subprocess.run(['systemd-analyze','verify',*[str(target) for name,target in targets.items() if name.startswith('deploy/')]],check=True,capture_output=True,timeout=25)
 system('daemon-reload');system('start','signaldesk-task-api');assert healthy(),'API health failed'
 result={'passed':True,'phase':'code_and_reconciliation_applied_activation_pending','backup':str(backup),'merge':merge,'private_config_created':True,'private_config_parent_created_identity':config_parent_identity,'start_date':a.start_date,'manager_timezone':manager_timezone,'manager_ntp_synchronized':True,'timer_local_timezone_dependency_verified':True,'legacy_guard_only_change':True,'legacy_guard_enabled':False,'timers_enabled':False,'article_body_or_user_state_changed':False,'schema_existing_migration_changed':False,'task_source_sha256':{name:digest(target.read_bytes()) for name,target in targets.items()},'collector_guard_sha256':digest(legacy.read_bytes()),'api_healthy':True,'messages_sent':0}
except Exception as error:
 rollback_data=None
 try:
  system('stop','signaldesk-task-api')
  if before is not None and merge is not None:
   with SessionLocal() as db:
    current_digest=object_digest(capture(db))
    # A failed/lost commit can leave the exact before-image. It is already safe
    # to roll back files; any third image must remain untouched.
    if current_digest!=object_digest(before):restore(db,before,merge['after_digest']);db.commit()
    rollback_data=True
  else:rollback_data=True
 except Exception as rollback_error:
  rollback_data=False;rollback_errors.append({'stage':'rollback_data_or_stop','error_type':type(rollback_error).__name__})
 private_cleanup={'config':'not_created' if config_identity is None else 'retained_data_not_restored','state':'not_created' if not state_created else 'retained_data_not_restored'}
 if rollback_data:
  for name,target in {**targets,'collector-main.py':legacy}.items():
   try:
    if saved[name] is None:
     if target.exists():
      if name in payload and not target.is_symlink() and digest(target.read_bytes())==digest(payload[name]):target.unlink()
      else:rollback_errors.append({'stage':'rollback_source_changed','path':name,'error_type':'IdentityChanged'})
    else:
     m=meta[name];atomic(target,saved[name],m['uid'],m['gid'],m['mode'])
   except Exception as rollback_error:rollback_errors.append({'stage':'rollback_source','path':name,'error_type':type(rollback_error).__name__})
  private_cleanup,cleanup_errors=cleanup_private_paths(config,config_identity,state,state_identity)
  if state_created and state_identity is None:private_cleanup['state']='retained_creation_identity_unavailable'
  rollback_errors.extend(cleanup_errors)
 result={'passed':False,'backup':str(backup),'error_type':type(error).__name__,'rollback_data':rollback_data,'private_cleanup':private_cleanup,'private_config_parent_created_identity':config_parent_identity,'messages_sent':0}
finally:
 available,restart_errors=ensure_api_started()
 if result is not None:
  result['api_restart_errors']=restart_errors;result['rollback_errors']=rollback_errors
  if result['passed']:
   result['api_healthy']=available
   if not available or restart_errors:result['passed']=False;result['phase']='applied_api_restart_attention_required'
  else:result['rollback_api_healthy']=available

if result is not None:
 try:(backup/('applied.json' if result['passed'] else 'failed.json')).write_text(json.dumps(result,indent=2))
 except Exception as error:
  result['passed']=False;result['evidence_error_type']=type(error).__name__
 print(json.dumps(result))
 if not result['passed']:raise SystemExit(1)
