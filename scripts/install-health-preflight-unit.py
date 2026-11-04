"""Run the prepared isolated collection test on legacy systemd using a real unit."""
import argparse,hashlib,json,os,pwd,re,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--stage-id',required=True);a=p.parse_args();assert re.fullmatch('[a-f0-9]{12}',a.stage_id)
unit='signaldesk-health-preflight-'+a.stage_id;root=Path('/var/lib')/unit
assert root.is_dir() and not root.is_symlink()
meta=json.loads((root/'acceptance.json').read_text());assert meta['unit']==unit+'.service' and meta['messages_sent']==0 and meta['prepare_only']
assert hashlib.sha256((root/'runtime/app/health_delivery.py').read_bytes()).hexdigest()==meta['source_sha256']=='9c7a8995c26456aef4cab1d24b9e5f9bed490a1735279a4a9fffc0f8bb20138d'
path=Path('/etc/systemd/system')/(unit+'.service');assert not path.exists()
source=f'''[Unit]
Description=Isolated health prepare-only acceptance
After=network-online.target
[Service]
Type=oneshot
User=signaldesk-task
Group=signaldesk-task
WorkingDirectory={root}/runtime
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/signaldesk-task/.venv/bin/python -m app.health_delivery --mode prepare --state-dir {root}/state --collector-root /opt/signaldesk --config {root}/config.env --test-run --run-id {meta['run_id']}
TimeoutStartSec=35min
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWriteDirectories={root}
'''
with path.open('x') as stream:stream.write(source)
path.chmod(0o644)
verify=subprocess.run(['systemd-analyze','verify',str(path)],capture_output=True,timeout=20)
result={'unit':unit+'.service','stage':str(root),'unit_file_sha256':hashlib.sha256(source.encode()).hexdigest(),'verify_exit_code':verify.returncode,'prepare_only':True,'messages_sent':0,'task_database_written':False,'production_timers_or_config_changed':False}
if verify.returncode:result['verify_error']=verify.stderr.decode(errors='replace')[:1500]
else:
 subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=20)
 started=subprocess.run(['systemctl','start','--no-block',unit+'.service'],capture_output=True,timeout=20)
 result['start_exit_code']=started.returncode
 if started.returncode:result['start_error']=started.stderr.decode(errors='replace')[:1500]
(root/'unit-acceptance.json').write_text(json.dumps(result));(root/'unit-acceptance.json').chmod(0o600)
print(json.dumps(result))
raise SystemExit(result.get('start_exit_code',verify.returncode))
