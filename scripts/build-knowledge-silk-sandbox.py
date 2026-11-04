"""Build the statically reviewed codec as nobody, offline and without app access."""
import argparse,hashlib,json,os,pwd,subprocess,tarfile,time,uuid,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--wheel-tool',type=Path,required=True);a=p.parse_args()
expected='d4a1bcf93dc6ef5e95e0cfd728ed4ef4d49f9c0476d70816fecbe456cc762e7f'
assert a.archive.parent==Path('/var/tmp') and a.archive.name.startswith('signaldesk-silk-source-') and not a.archive.is_symlink()
assert hashlib.sha256(a.archive.read_bytes()).hexdigest()==expected
os.umask(0o077);stage=Path('/var/tmp')/('signaldesk-silk-compile-'+uuid.uuid4().hex[:10]);stage.mkdir(mode=0o755);stage.chmod(0o755)
work=stage/'work';work.mkdir(mode=0o700);owner=pwd.getpwnam('nobody');os.chown(work,owner.pw_uid,owner.pw_gid)
with tarfile.open(a.archive) as tar:
    for m in tar.getmembers():
        parts=Path(m.name).parts
        assert parts[0]=='pilk-0.2.4' and '..' not in parts and not m.issym() and not m.islnk()
        target=work/Path(*parts[1:])
        assert target.resolve().is_relative_to(work.resolve())
        if m.isdir():target.mkdir(exist_ok=True,parents=True);target.chmod(0o755)
        else:
            assert m.isfile();target.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as src,target.open('xb') as out:out.write(src.read())
            target.chmod(0o644)
    for target in work.rglob('*'):os.chown(target,owner.pw_uid,owner.pw_gid)
# A pure Python build tool is downloaded by the controller, then made read-only
# to the sandbox. The build itself cannot reach package registries or app files.
assert a.wheel_tool.parent==Path('/var/tmp') and not a.wheel_tool.is_symlink()
data=a.wheel_tool.read_bytes()
assert hashlib.sha256(data).hexdigest()=='708e7481cc80179af0e556bbf0cc00b8444c7321e2700b8d8580231d13017248'
tool=stage/'wheel-0.45.1.whl';tool.write_bytes(data);deps=stage/'build-deps';deps.mkdir(mode=0o755);deps.chmod(0o755)
with zipfile.ZipFile(tool) as z:
    for member in z.infolist():
        target=deps/member.filename;assert target.resolve().is_relative_to(deps.resolve())
        if member.is_dir():target.mkdir(parents=True,exist_ok=True)
        else:
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(member))
for target in deps.rglob('*'):target.chmod(0o755 if target.is_dir() else 0o644)
source_audit={'source_sha256':expected,'files':167,'setup':'setuptools.Extension of local C files only','external_exec_or_network_calls_found':False}
wrapper='''import json,os,runpy,socket,sys
log=open('build.log','w',buffering=1)
os.dup2(log.fileno(),1);os.dup2(log.fileno(),2)
assert os.geteuid()!=0
try:open('/opt/signaldesk-task/.env','rb')
except (PermissionError,FileNotFoundError):pass
else:raise RuntimeError('Production configuration is readable')
try:socket.create_connection(('1.1.1.1',443),timeout=1)
except OSError:pass
else:raise RuntimeError('Network socket is available')
print(json.dumps({'sandbox_uid':os.geteuid(),'app_config_inaccessible':True,'network_disabled':True}),flush=True)
sys.argv=['setup.py','bdist_wheel','--dist-dir=wheelhouse']
runpy.run_path('setup.py',run_name='__main__')
'''
unit='signaldesk-silk-build-'+uuid.uuid4().hex[:8]
properties=['User='+owner.pw_name,'Group='+str(owner.pw_gid),'WorkingDirectory='+str(work),'NoNewPrivileges=yes',
    'PrivateNetwork=yes','PrivateDevices=yes','ProtectSystem=full','ProtectHome=yes',
    'ReadOnlyDirectories=/','ReadWriteDirectories='+str(work),'InaccessibleDirectories=/opt /root /home /var/backups','RestrictAddressFamilies=AF_UNIX',
    'CapabilityBoundingSet=','MemoryLimit=805306368','CPUQuota=50%','TimeoutStartSec=180','Type=oneshot','RemainAfterExit=yes',
    'Environment=PYTHONNOUSERSITE=1 PYTHONPATH='+str(deps)+' TMPDIR='+str(work/'tmp')]
(work/'tmp').mkdir();os.chown(work/'tmp',owner.pw_uid,owner.pw_gid)
runner=stage/'sandbox-build.py';runner.write_text(wrapper);runner.chmod(0o644)
unit_file=Path('/run/systemd/system')/(unit+'.service')
assert not unit_file.exists()
unit_file.write_text('[Unit]\nDescription=Isolated reviewed SILK build\n[Service]\n'+'\n'.join(properties)+'\nExecStart=/usr/local/bin/python3.10 '+str(runner)+'\n');unit_file.chmod(0o644)
subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=15)
r=subprocess.run(['systemctl','start','--no-block',unit],capture_output=True,text=True,timeout=15)
if r.returncode==0:
    for _ in range(95):
        time.sleep(2)
        status=subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','ExecMainStatus'],capture_output=True,text=True,timeout=10).stdout
        if 'ActiveState=activating' not in status:break
    else:raise RuntimeError('Build did not finish')
    r.returncode=0 if 'ActiveState=active' in status and 'ExecMainStatus=0' in status else 1
    r.stderr=status
    subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=15)
    subprocess.run(['systemctl','reset-failed',unit],capture_output=True,timeout=15)
unit_file.unlink();subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=15)
(stage/'build.private.log').write_text((work/'build.log').read_text() if (work/'build.log').exists() else r.stdout+'\n'+r.stderr)
if r.returncode:
    print(json.dumps({'passed':False,'stage':str(stage),'step':'restricted build','code':r.returncode,'diagnostic':r.stderr.splitlines()[-1][:180] if r.stderr else ''}));raise SystemExit(1)
wheels=list((work/'wheelhouse').glob('pilk-0.2.4-*.whl'));assert len(wheels)==1
wheel=stage/wheels[0].name;wheel.write_bytes(wheels[0].read_bytes());wheel.chmod(0o600)
with zipfile.ZipFile(wheel) as z:assert z.testzip() is None
result={'passed':True,'stage':str(stage),'wheel':str(wheel),'wheel_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
    'source_audit':source_audit,'sandbox_uid':owner.pw_uid,'network_disabled':True,'app_config_inaccessible':True,'production_changed':False}
(stage/'dependency-manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
