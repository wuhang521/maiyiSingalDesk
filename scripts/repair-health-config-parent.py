"""Repair only this deployment's new private config parent after umask masking."""
import hashlib,json,os,pwd,stat
from pathlib import Path
from app.database import SessionLocal
from app.health_delivery import Config
from app.models import SmartChannel

backup=Path('/var/backups/signaldesk-task/health-push-20261030T024150Z-1e75b8')
applied=json.loads((backup/'applied.json').read_text());assert applied['passed'] and applied['private_config_created']
parent=Path('/etc/signaldesk');config=parent/'health-delivery.env';account=pwd.getpwnam('signaldesk-task')
assert not parent.is_symlink() and not config.is_symlink() and sorted(item.name for item in parent.iterdir())==['health-delivery.env']
info=parent.stat();assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700 and info.st_uid==0 and info.st_gid==account.pw_gid
value=config.stat();assert stat.S_IMODE(value.st_mode)==0o600 and value.st_uid==account.pw_uid and value.st_gid==account.pw_gid
before=hashlib.sha256(config.read_bytes()).hexdigest();destination=Config.load(config)
with SessionLocal() as db:
 channel=db.get(SmartChannel,'health');assert channel.server==destination.server and channel.topic==destination.topic
os.chmod(parent,0o750)
after=parent.stat();assert after.st_uid==info.st_uid and after.st_gid==info.st_gid and after.st_ino==info.st_ino and stat.S_IMODE(after.st_mode)==0o750
assert before==hashlib.sha256(config.read_bytes()).hexdigest() and config.stat().st_ino==value.st_ino
result={'passed':True,'path':'/etc/signaldesk','created_by_this_deployment_verified':True,'before_mode':'0700','after_mode':'0750','owner_or_group_changed':False,'private_config_content_or_0600_mode_changed':False,'existing_shared_directories_changed':False,'messages_sent':0}
(backup/'config-parent-repair.json').write_text(json.dumps(result));print(json.dumps(result))
