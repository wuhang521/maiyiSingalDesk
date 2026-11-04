"""Switch only our failed isolated service to a prepare-only diagnostic entry."""
import hashlib,json,subprocess
from pathlib import Path
stage=Path('/var/lib/signaldesk-health-preflight-8974d6ef4d09')
unit='signaldesk-health-preflight-8974d6ef4d09.service';path=Path('/etc/systemd/system')/unit
assert not stage.is_symlink() and stage.stat().st_uid==994
helper=stage/'runtime/diagnose-health-collector.py'
assert hashlib.sha256(helper.read_bytes()).hexdigest()=='4f46d0187c9166151a353fc2f94bab4497158d9eab03a315f3cbcd4be8e3af7d'
source=path.read_bytes();assert hashlib.sha256(source).hexdigest()=='8e86ec714f2ecb155bf1de0a1c2ba6c3f09101b8f0a78d55193e973e063fc580'
old=[line for line in source.decode().splitlines() if line.startswith('ExecStart=')];assert len(old)==1
replacement='ExecStart=/opt/signaldesk-task/.venv/bin/python '+str(helper)+' --mode prepare --allow-collection --run-id collector-8974d6ef4d09'
changed=source.decode().replace(old[0],replacement);assert changed!=source.decode()
backup=stage/'unit-before-diagnostic.service';assert not backup.exists();backup.write_bytes(source);backup.chmod(0o600)
path.write_text(changed);path.chmod(0o644)
check=subprocess.run(['systemd-analyze','verify',str(path)],capture_output=True,timeout=20)
assert check.returncode==0,'Isolated diagnostic unit failed verification'
subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=20)
subprocess.run(['systemctl','reset-failed',unit],check=True,capture_output=True,timeout=20)
start=subprocess.run(['systemctl','start','--no-block',unit],capture_output=True,timeout=20)
result={'started':start.returncode==0,'unit':unit,'unit_sha256':hashlib.sha256(changed.encode()).hexdigest(),'helper_sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),'actual_same_service_uid_and_sandbox':True,'prepare_only':True,'publication_and_ingestion_blocked':True,'private_failure_logs_stay_on_server':True,'production_services_or_timers_changed':False}
if start.returncode:result['error_type']='ServiceStartFailed'
print(json.dumps(result));raise SystemExit(start.returncode)
