"""Packaged native-voice acceptance; temporary identity has no notification channel."""
from pathlib import Path
import argparse,json,socket,subprocess,tempfile,time
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--package-dir',type=Path,required=True);p.add_argument('--sample',type=Path,required=True);p.add_argument('--node',default='node');a=p.parse_args()
out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
assert a.package_dir.is_dir() and a.sample.is_file()
opts=['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes'];ssh=['ssh',*opts,'hermes-desktop-vps'];code=(root/'scripts/knowledge-production-account.py').read_text(encoding='utf-8')
r=subprocess.run([*ssh,'/opt/signaldesk-task/.venv/bin/python','-','create','--no-channel'],input=code,capture_output=True,text=True,encoding='utf-8',timeout=30);assert r.returncode==0,'Test identity creation failed'
login=json.loads(r.stdout);marker=login['marker'];(out/'desktop-smoke-state.json').write_text(json.dumps({'marker':marker,'test_identity_only':True}))
work=Path(tempfile.mkdtemp(prefix='kb-live-'));credentials=work/'login.json';credentials.write_text(json.dumps(login),encoding='utf-8')
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
tunnel=subprocess.Popen(['ssh',*opts,'-o','ExitOnForwardFailure=yes','-N','-L',f'127.0.0.1:{port}:127.0.0.1:19080','hermes-desktop-vps'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
status=1
try:
 for _ in range(50):
  try:
   with socket.create_connection(('127.0.0.1',port),timeout=.5):break
  except OSError:time.sleep(.2)
 else:raise RuntimeError('Tunnel unavailable')
 with (out/'desktop-silk-live-final.log').open('w',encoding='utf-8') as log:
  r=subprocess.run([a.node,'scripts/knowledge-silk-desktop-e2e.mjs',str(credentials),f'http://127.0.0.1:{port}',str(out/'evidence/desktop-silk-live-final'),str(a.package_dir.resolve()),str(a.sample.resolve())],cwd=root,stdout=log,stderr=log,timeout=210)
 status=r.returncode;print(json.dumps({'desktop_code':status,'qq_sent':False}))
finally:
 tunnel.terminate();tunnel.wait(timeout=10);credentials.unlink(missing_ok=True);work.rmdir()
 clean=subprocess.run([*ssh,'/opt/signaldesk-task/.venv/bin/python','-','cleanup','--marker',marker],input=code,capture_output=True,text=True,encoding='utf-8',timeout=40)
 (out/'desktop-test-cleanup-final.json').write_text(clean.stdout,encoding='utf-8');print(clean.stdout.strip())
 if clean.returncode:status=1
raise SystemExit(status)
