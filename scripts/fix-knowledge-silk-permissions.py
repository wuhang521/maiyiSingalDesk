"""Normalize only verified codec files and validate as the actual service user."""
import argparse,hashlib,json,os,subprocess,time,urllib.request,uuid,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--asr',type=Path,required=True);p.add_argument('--sha256',required=True);a=p.parse_args()
assert a.asr.parent==Path('/var/tmp') and a.asr.name.startswith('signaldesk-silk-asr-')
assert hashlib.sha256(a.asr.read_bytes()).hexdigest()==a.sha256
root=Path('/opt/signaldesk-task');python=root/'.venv/bin/python';site=root/'.venv/lib/python3.10/site-packages'
backup=Path('/var/backups/signaldesk-task/silk-062-20261028T035441Z-cc1626');assert backup.is_dir()
wheel=Path('/var/tmp/signaldesk-silk-compile-3842ce1f6d/pilk-0.2.4-cp310-cp310-linux_x86_64.whl')
assert hashlib.sha256(wheel.read_bytes()).hexdigest()=='c74383aa95be28c7c4f6a18152d0c586bfabb3884f93570ad4b86a1188cbc14e'
metadata={}
with zipfile.ZipFile(wheel) as z:
 for n in z.namelist():
  path=site/n;assert path.resolve().is_relative_to(site.resolve()) and not path.is_symlink()
  # pip updates RECORD when adding its installer metadata; codec code is exact.
  if not n.endswith('.dist-info/RECORD'):
   assert hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256(z.read(n)).digest(),n
  metadata[n]=oct(path.stat().st_mode&511)
 for folder in [site/'pilk',site/'pilk-0.2.4.dist-info']:
  folder.chmod(0o755)
  for path in folder.rglob('*'):
   assert not path.is_symlink();path.chmod(0o755 if path.is_dir() else 0o644)
target=root/'app/knowledge_asr.py';before=target.stat();(backup/'knowledge_asr-before-readiness-fix.py').write_bytes(target.read_bytes())
temp=target.with_name('.knowledge_asr-'+uuid.uuid4().hex[:8]);temp.write_bytes(a.asr.read_bytes());os.chown(temp,before.st_uid,before.st_gid);temp.chmod(before.st_mode&511);os.replace(temp,target)
work=Path('/var/tmp')/('signaldesk-silk-service-test-'+uuid.uuid4().hex[:8]);work.mkdir(mode=0o700);os.chown(work,994,990)
source=work/'public.amr';source.write_bytes(Path('/var/tmp/signaldesk-silk-public.amr').read_bytes());source.chmod(0o600);os.chown(source,994,990)
cmd=[str(python),'-m','app.knowledge_silk',str(source),str(work/'decoded.wav')]
r=subprocess.run(cmd,user=994,group=990,extra_groups=[],cwd=root,env={'PATH':'/usr/local/bin:/usr/bin:/bin'},capture_output=True,timeout=45)
assert r.returncode==0,'Service-user actual decode failed'
r=subprocess.run([str(python),'-c','from app.knowledge_asr import silk_configuration;assert silk_configuration();print("ready")'],user=994,group=990,extra_groups=[],cwd=root,capture_output=True,timeout=15)
assert r.returncode==0,'Service-user readiness failed'
subprocess.run(['systemctl','restart','signaldesk-task-api','signaldesk-knowledge-worker'],check=True,capture_output=True,timeout=45)
for _ in range(20):
 try:
  with urllib.request.urlopen('http://127.0.0.1:19080/health',timeout=3) as r:assert r.status==200
  break
 except OSError:time.sleep(1)
else:raise RuntimeError('Health did not recover')
result={'passed':True,'actual_service_uid':994,'service_user_native_decode':True,'readiness_requires_callable_decode':True,'asr_sha256':a.sha256,'normalized_only_codec_files':True,'backup':str(backup),'qq_sent':False}
(backup/'permission-correction.json').write_text(json.dumps({'result':result,'before_file_modes':metadata},indent=2));print(json.dumps(result))
