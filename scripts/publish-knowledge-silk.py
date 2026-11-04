"""Publish the reviewed SILK increment; preserve metadata and roll back on failure."""
import argparse,hashlib,importlib.util,json,os,stat,subprocess,tarfile,time,urllib.request,uuid,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--sha256',required=True);p.add_argument('--codec-stage',type=Path,required=True);a=p.parse_args()
assert a.archive.parent==Path('/var/tmp') and a.archive.name.startswith('signaldesk-silk-increment-') and not a.archive.is_symlink()
assert hashlib.sha256(a.archive.read_bytes()).hexdigest()==a.sha256
root=Path('/opt/signaldesk-task');python=root/'.venv/bin/python'
assert a.codec_stage.parent==Path('/var/tmp') and a.codec_stage.name.startswith('signaldesk-silk-compile-')
manifest=json.loads((a.codec_stage/'dependency-manifest.json').read_text());wheel=Path(manifest['wheel'])
assert hashlib.sha256(wheel.read_bytes()).hexdigest()==manifest['wheel_sha256']
expected={'app/knowledge_silk.py','app/knowledge_asr.py','app/knowledge_extract.py','app/knowledge.py','requirements-silk.txt'}
with zipfile.ZipFile(wheel) as z:
 assert all(not n.endswith('.pth') and (n.startswith('pilk/') or n.startswith('pilk-0.2.4.dist-info/')) for n in z.namelist())
payload={}
with tarfile.open(a.archive) as t:
 for m in t:
  assert m.isfile() and m.name in expected and m.name not in payload
  payload[m.name]=t.extractfile(m).read()
assert set(payload)==expected
# Require real restricted codec acceptance, not only a successful C build.
proofs=list(a.codec_stage.glob('native-test-*/result.json'))
assert any(json.loads(x.read_text()).get('passed') for x in proofs)
os.umask(0o077);backup=Path('/var/backups/signaldesk-task')/('silk-062-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:6]);backup.mkdir(mode=0o700)
files={};metadata={}
for name in [*sorted(expected),'.env']:
 path=root/name
 if path.exists():
  info=path.stat();assert stat.S_ISREG(info.st_mode) and not path.is_symlink()
  files[name]=path.read_bytes();metadata[name]={'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode),'sha256':hashlib.sha256(files[name]).hexdigest()}
  target=backup/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(files[name])
 else:metadata[name]=None
(backup/'file-metadata.json').write_text(json.dumps(metadata,indent=2))
probe=subprocess.run([str(python),'-c','import importlib.util; print(importlib.util.find_spec("pilk") is not None)'],cwd=root,capture_output=True,text=True,check=True,timeout=10)
assert probe.stdout.strip()=='False','Decoder already exists; refusing to replace an unknown installation'
(backup/'pip-before.txt').write_text(subprocess.run([str(python),'-m','pip','freeze'],capture_output=True,text=True,check=True,timeout=20).stdout)
def replace(name,data,meta):
 target=root/name;temp=target.with_name('.'+target.name+'.silk-'+uuid.uuid4().hex[:8]);temp.write_bytes(data)
 os.chown(temp,meta['uid'],meta['gid']);temp.chmod(meta['mode']);os.replace(temp,target)
installed=False
try:
 r=subprocess.run([str(python),'-m','pip','install','--no-index','--no-deps','--disable-pip-version-check',str(wheel)],capture_output=True,text=True,timeout=40,preexec_fn=lambda:os.umask(0o022))
 (backup/'dependency-install.log').write_text(r.stdout+'\n'+r.stderr);assert r.returncode==0,'Wheel installation failed'
 installed=True
 for name,data in payload.items():replace(name,data,metadata[name] or {'uid':994,'gid':990,'mode':0o640})
 lines=files['.env'].decode().splitlines();lines=[line for line in lines if not line.startswith('TASK_KNOWLEDGE_SILK_ENABLED=')];lines.append('TASK_KNOWLEDGE_SILK_ENABLED=true')
 replace('.env',('\n'.join(lines)+'\n').encode(),metadata['.env'])
 check=subprocess.run([str(python),'-c','from app.knowledge_asr import silk_configuration; assert silk_configuration(); import pilk; assert callable(pilk.decode); print("ready")'],user=994,group=990,extra_groups=[],cwd=root,capture_output=True,text=True,timeout=15)
 assert check.returncode==0,'Installed decoder is not importable'
 subprocess.run(['systemctl','restart','signaldesk-task-api','signaldesk-knowledge-worker'],check=True,capture_output=True,timeout=45)
 for _ in range(20):
  try:
   with urllib.request.urlopen('http://127.0.0.1:19080/health',timeout=3) as r:healthy=r.status==200
   if healthy:break
  except OSError:pass
  time.sleep(1)
 else:raise RuntimeError('Health did not recover')
 services={name:subprocess.run(['systemctl','is-active',name],capture_output=True,text=True,timeout=10).stdout.strip() for name in ['signaldesk-task-api','signaldesk-task-worker','signaldesk-knowledge-worker','signaldesk-knowledge-dispatch']}
 assert all(v=='active' for v in services.values())
 result={'passed':True,'backup':str(backup),'codec_version':'0.2.4','wheel_sha256':manifest['wheel_sha256'],'silk_enabled':True,'services':services,'code_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(expected)},'schema_changed':False,'qq_sent':False}
 (backup/'published.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
except Exception as error:
 for name,meta in metadata.items():
  if meta:replace(name,files[name],meta)
  elif (root/name).exists():(root/name).unlink()
 if installed:subprocess.run([str(python),'-m','pip','uninstall','-y','pilk'],capture_output=True,timeout=30)
 subprocess.run(['systemctl','restart','signaldesk-task-api','signaldesk-knowledge-worker'],capture_output=True,timeout=45)
 print(json.dumps({'passed':False,'backup':str(backup),'rolled_back':True,'error_type':type(error).__name__}));raise SystemExit(1)
