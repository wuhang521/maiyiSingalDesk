"""Publish only the two verified ingress fixes; preserve metadata and recover on failure."""
import argparse,hashlib,json,os,subprocess,tarfile,time,urllib.request,uuid
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--sha256',required=True);a=p.parse_args()
assert a.archive.parent==Path('/var/tmp') and a.archive.name.startswith('signaldesk-kb063-publish-') and not a.archive.is_symlink()
assert hashlib.sha256(a.archive.read_bytes()).hexdigest()==a.sha256
targets={'server/app/knowledge_gateway.py':Path('/opt/signaldesk-task/app/knowledge_gateway.py'),'hermes-plugin/wechat-knowledge/__init__.py':Path('/home/hermes/.hermes/plugins/wechat-knowledge/__init__.py')}
old_hashes={'server/app/knowledge_gateway.py':'1028f3622baf8776584f27ade3a831c4ad37e9cd920317ca889ed33eb07e70af','hermes-plugin/wechat-knowledge/__init__.py':'cdce869928c47e60976b1cced2a7609c5e8783cb81a354dce4cb71617c9521f9'}
payload={}
with tarfile.open(a.archive) as tar:
 for member in tar:
  assert member.isfile() and member.name in targets and member.name not in payload
  payload[member.name]=tar.extractfile(member).read();compile(payload[member.name],member.name,'exec')
assert set(payload)==set(targets)
for name,path in targets.items():assert not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest()==old_hashes[name],'Live source differs; refusing to overwrite'
os.umask(0o077);backup=Path('/var/backups/signaldesk-task')/('knowledge-063-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:6]);backup.mkdir(mode=0o700)
saved={};metadata={}
for name,path in targets.items():
 info=path.stat();saved[name]=path.read_bytes();metadata[name]={'uid':info.st_uid,'gid':info.st_gid,'mode':info.st_mode&511,'sha256':old_hashes[name]};dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(saved[name])
(backup/'file-metadata.json').write_text(json.dumps(metadata,indent=2))
config=Path('/home/hermes/.hermes/config.yaml');config_sha=hashlib.sha256(config.read_bytes()).hexdigest()
def replace(path,content,meta):
 temporary=path.with_name('.'+path.name+'.kb063-'+uuid.uuid4().hex[:8]);temporary.write_bytes(content);os.chown(temporary,meta['uid'],meta['gid']);temporary.chmod(meta['mode']);os.replace(temporary,path)
def health():
 for _ in range(30):
  try:
   with urllib.request.urlopen('http://127.0.0.1:19080/health',timeout=3) as r:api=r.status==200
   hermes=subprocess.run(['docker','inspect','-f','{{.State.Running}}','hermes'],capture_output=True,text=True,timeout=10).stdout.strip()=='true'
   if api and hermes:return True
  except OSError:pass
  time.sleep(1)
 return False
try:
 for name,path in targets.items():replace(path,payload[name],metadata[name])
 # Load the exact mounted plugin with its real Python/SDK, without dispatching a message.
 code="import importlib.util; p='/opt/data/plugins/wechat-knowledge/__init__.py'; s=importlib.util.spec_from_file_location('kb063_probe',p); m=importlib.util.module_from_spec(s);s.loader.exec_module(m);assert callable(m.import_attachments);import gateway.platforms.base;print('ready')"
 check=subprocess.run(['docker','exec','-e','HERMES_HOME=/opt/data','hermes','/opt/hermes/.venv/bin/python','-c',code],capture_output=True,text=True,timeout=30)
 assert check.returncode==0,'Mounted plugin/SDK import failed'
 subprocess.run(['systemctl','restart','signaldesk-task-api'],check=True,capture_output=True,timeout=40)
 subprocess.run(['docker','restart','hermes'],check=True,capture_output=True,timeout=60)
 assert health(),'API or main Hermes did not recover'
 assert hashlib.sha256(config.read_bytes()).hexdigest()==config_sha,'Main configuration changed unexpectedly'
 result={'passed':True,'version':'0.6.3','backup':str(backup),'files':{name:{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'uid':path.stat().st_uid,'gid':path.stat().st_gid,'mode':oct(path.stat().st_mode&511)} for name,path in targets.items()},'plugin_real_sdk_imported':True,'api_healthy':True,'main_hermes_running':True,'hermes_configuration_unchanged':True,'database_schema_changed':False,'qq_tested':False,'external_test_message_sent':False}
 (backup/'published.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
except Exception as error:
 for name,path in targets.items():replace(path,saved[name],metadata[name])
 subprocess.run(['systemctl','restart','signaldesk-task-api'],capture_output=True,timeout=40);subprocess.run(['docker','restart','hermes'],capture_output=True,timeout=60)
 print(json.dumps({'passed':False,'backup':str(backup),'error_type':type(error).__name__,'rollback_attempted':True,'rollback_healthy':health()}));raise SystemExit(1)
