"""Start one isolated prepare-only systemd test. No sends and no task DB writes."""
import argparse, hashlib, io, json, os, pwd, subprocess, tarfile, uuid
from pathlib import Path
from src.main import load_config

p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--sha256',required=True);a=p.parse_args()
assert a.archive.parent==Path('/var/tmp') and a.archive.name.startswith('signaldesk-health-runtime-') and not a.archive.is_symlink()
assert hashlib.sha256(a.archive.read_bytes()).hexdigest()==a.sha256
expected={'app/__init__.py','app/health_delivery.py','app/schemas.py'}
payload={}
with tarfile.open(a.archive) as tar:
 for member in tar:
  assert member.isfile() and member.name in expected and member.name not in payload
  data=tar.extractfile(member).read();compile(data,member.name,'exec');payload[member.name]=data
assert set(payload)==expected
assert hashlib.sha256(payload['app/health_delivery.py']).hexdigest()=='9c7a8995c26456aef4cab1d24b9e5f9bed490a1735279a4a9fffc0f8bb20138d'
identity=uuid.uuid4().hex[:12];unit='signaldesk-health-preflight-'+identity
root=Path('/var/lib')/unit
account=pwd.getpwnam('signaldesk-task');uid,gid=account.pw_uid,account.pw_gid
os.umask(0o077);root.mkdir(mode=0o700);os.chown(root,uid,gid)
runtime=root/'runtime';runtime.mkdir(mode=0o700);os.chown(runtime,uid,gid)
for name,data in payload.items():
 target=runtime/name;target.parent.mkdir(mode=0o700,exist_ok=True);os.chown(target.parent,uid,gid)
 target.write_bytes(data);target.chmod(0o600);os.chown(target,uid,gid)
config=load_config(Path('/opt/signaldesk/.env'));health=next(r for r in config.recipients if r.key=='health')
private=root/'config.env';private.write_text('NTFY_SERVER='+config.ntfy_server+'\nHEALTH_NTFY_TOPIC='+health.topic+'\nHEALTH_MAX_ITEMS=3\nHEALTH_START_DATE=2026-10-31\nCOLLECTOR_PYTHON=/opt/signaldesk/.venv/bin/python\n');private.chmod(0o600);os.chown(private,uid,gid)
state=root/'state';state.mkdir(mode=0o700);os.chown(state,uid,gid)
run_id='collector-'+identity
command=['systemd-run','--unit='+unit,'--property=Type=oneshot','--property=User=signaldesk-task','--property=Group=signaldesk-task','--property=WorkingDirectory='+str(runtime),'--property=NoNewPrivileges=true','--property=PrivateTmp=true','--property=ProtectHome=true','--property=ProtectSystem=full','--property=UMask=0077','--property=TimeoutStartSec=35min','--property=ReadWriteDirectories='+str(root),'--setenv=PYTHONDONTWRITEBYTECODE=1','--setenv=PYTHONUNBUFFERED=1','/opt/signaldesk-task/.venv/bin/python','-m','app.health_delivery','--mode','prepare','--state-dir',str(state),'--collector-root','/opt/signaldesk','--config',str(private),'--test-run','--run-id',run_id]
result=subprocess.run(command,capture_output=True,timeout=20)
metadata={'started':result.returncode==0,'unit':unit+'.service','state':str(state),'run_id':run_id,'prepare_only':True,'source_sha256':hashlib.sha256(payload['app/health_delivery.py']).hexdigest(),'production_config_or_timers_changed':False,'messages_sent':0,'task_database_written':False,'max_items':3,'service_sandbox_matches':True}
if result.returncode:metadata['start_error']=result.stderr.decode('utf-8',errors='replace')[:2000]
(root/'acceptance.json').write_text(json.dumps(metadata));(root/'acceptance.json').chmod(0o600)
print(json.dumps(metadata));raise SystemExit(result.returncode)
