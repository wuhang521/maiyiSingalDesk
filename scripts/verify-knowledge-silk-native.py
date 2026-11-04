"""Offline actual-codec acceptance as nobody, outside the production application."""
import argparse,hashlib,json,os,pwd,subprocess,time,uuid,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--decoder',type=Path,required=True);p.add_argument('--sample',type=Path,required=True);a=p.parse_args()
assert a.stage.parent==Path('/var/tmp') and a.stage.name.startswith('signaldesk-silk-compile-')
manifest=json.loads((a.stage/'dependency-manifest.json').read_text());wheel=Path(manifest['wheel'])
assert hashlib.sha256(wheel.read_bytes()).hexdigest()==manifest['wheel_sha256']
assert a.decoder.parent==a.sample.parent==Path('/var/tmp')
assert hashlib.sha256(a.sample.read_bytes()).hexdigest()=='e267dac1cef824b188e5c8659a513bbe147dc140fbefdf90ec2fc92ea0c734f3'
os.umask(0o077);work=a.stage/('native-test-'+uuid.uuid4().hex[:8]);work.mkdir();owner=pwd.getpwnam('nobody');os.chown(work,owner.pw_uid,owner.pw_gid)
vendor=work/'vendor';vendor.mkdir();vendor.chmod(0o755)
with zipfile.ZipFile(wheel) as z:
    for member in z.infolist():
        target=vendor/member.filename;assert target.resolve().is_relative_to(vendor.resolve())
        if member.is_dir():target.mkdir(parents=True,exist_ok=True)
        else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(member))
for target in vendor.rglob('*'):target.chmod(0o755 if target.is_dir() else 0o644)
(vendor/'typing_extensions.py').write_bytes(Path('/opt/signaldesk-task/.venv/lib/python3.10/site-packages/typing_extensions.py').read_bytes());(vendor/'typing_extensions.py').chmod(0o644)
(work/'knowledge_silk.py').write_bytes(a.decoder.read_bytes());(work/'knowledge_silk.py').chmod(0o644)
(work/'sample.amr').write_bytes(a.sample.read_bytes());(work/'sample.amr').chmod(0o644)
runner=work/'accept.py'
runner.write_text('''import array,ctypes,hashlib,json,math,os,pathlib,socket,wave
import pilk,knowledge_silk as codec
assert os.geteuid()!=0
try:open('/opt/signaldesk-task/.env','rb')
except (OSError,):pass
else:raise AssertionError('production readable')
try:socket.create_connection(('1.1.1.1',443),timeout=1)
except OSError:pass
else:raise AssertionError('network reachable')
root=pathlib.Path.cwd();source=root/'sample.amr';output=root/'public-decoded.wav'
codec.decode(source,output)
with wave.open(str(output)) as w:
 assert w.getnchannels()==1 and w.getsampwidth()==2 and w.getframerate()==24000
 duration=w.getnframes()/w.getframerate();pcm=array.array('h',w.readframes(w.getnframes()))
 assert 0<duration<600 and max(abs(x) for x in pcm)>100
 public={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'wav_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'duration':duration,'frames':len(pcm),'peak':max(abs(x) for x in pcm)}
raw=root/'tone.pcm';raw.write_bytes(array.array('h',(int(10000*math.sin(i*2*math.pi*440/24000)) for i in range(48000))).tobytes())
roundtrips=[]
for tencent in [False,True]:
 silk=root/('tone-tencent.silk' if tencent else 'tone-standard.silk')
 pilk.encode(str(raw),str(silk),pcm_rate=24000,tencent=tencent)
 ctypes.CDLL(None).fflush(None)
 wav=silk.with_suffix('.wav');codec.decode(silk,wav)
 with wave.open(str(wav)) as w:
  length=w.getnframes()/w.getframerate();assert 1.9<=length<=2.1
 roundtrips.append({'tencent':tencent,'duration':length,'packets':codec.validate_packets(silk.read_bytes()),'synthetic':True})
rejected=[]
for name,data in [('truncated',source.read_bytes()[:-1]),('oversize-length',b'#!SILK_V3'+b'\\xff\\x7f'),('empty',b'#!SILK_V3'),('short',b'#!SILK_V3'+b'\\x01\\x00x'*2)]:
 path=root/(name+'.silk');path.write_bytes(data);target=root/(name+'.wav')
 try:codec.decode(path,target)
 except ValueError:pass
 else:raise AssertionError('Invalid input accepted: '+name)
 assert not target.exists();rejected.append(name)
result={'passed':True,'public_tencent_fixture':public,'synthetic_roundtrips':roundtrips,'malformed_rejected':rejected,'sandbox_uid':os.geteuid(),'production_inaccessible':True,'network_disabled':True,'production_changed':False}
(root/'result.json').write_text(json.dumps(result,indent=2))
''');runner.chmod(0o644)
unit='signaldesk-silk-verify-'+uuid.uuid4().hex[:8];unit_file=Path('/run/systemd/system')/(unit+'.service')
properties=['Type=oneshot','RemainAfterExit=yes','User='+owner.pw_name,'Group='+str(owner.pw_gid),'WorkingDirectory='+str(work),
 'NoNewPrivileges=yes','PrivateNetwork=yes','PrivateDevices=yes','ProtectSystem=full','ProtectHome=yes','ReadOnlyDirectories=/',
 'ReadWriteDirectories='+str(work),'InaccessibleDirectories=/opt /root /home /var/backups','CapabilityBoundingSet=',
 'MemoryLimit=805306368','CPUQuota=50%','TimeoutStartSec=60','Environment=PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='+str(vendor),
 'ExecStart=/usr/local/bin/python3.10 '+str(runner)]
assert not unit_file.exists();unit_file.write_text('[Unit]\nDescription=Offline SILK acceptance\n[Service]\n'+'\n'.join(properties)+'\n');unit_file.chmod(0o644)
try:
 subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=15)
 subprocess.run(['systemctl','start','--no-block',unit],check=True,capture_output=True,timeout=10)
 for _ in range(35):
  time.sleep(2);status=subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','ExecMainStatus'],capture_output=True,text=True,timeout=10).stdout
  if 'ActiveState=activating' not in status:break
 if not (work/'result.json').exists():
  print(json.dumps({'passed':False,'work':str(work),'status':status,'log':subprocess.run(['journalctl','-u',unit,'--no-pager','-n','15'],capture_output=True,text=True,timeout=10).stdout[-2000:]}));raise SystemExit(1)
 result=json.loads((work/'result.json').read_text());result['work']=str(work);print(json.dumps(result))
finally:
 subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=10);subprocess.run(['systemctl','reset-failed',unit],capture_output=True,timeout=10)
 unit_file.unlink();subprocess.run(['systemctl','daemon-reload'],capture_output=True,timeout=15)
