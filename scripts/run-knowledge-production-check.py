"""Windows final-client acceptance over SSH; all credentials/data are synthetic."""
from pathlib import Path
import json,socket,subprocess,tempfile,time
root=Path(__file__).resolve().parents[1]
base=Path('D:/maiyi/docs/delivery/knowledge-complete-20261028')
assert (base/'production-published.json').exists()
opts=['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes'];ssh=['ssh',*opts,'hermes-desktop-vps']
code=(root/'scripts/knowledge-production-account.py').read_text(encoding='utf-8')
r=subprocess.run([*ssh,'/opt/signaldesk-task/.venv/bin/python','-','create'],input=code,capture_output=True,text=True,encoding='utf-8',timeout=30)
if r.returncode:print(json.dumps({'passed':False,'step':'create synthetic identity'}));raise SystemExit(r.returncode)
login=json.loads(r.stdout);marker=login['marker']
(base/'production-smoke-state.json').write_text(json.dumps({'marker':marker,'synthetic_only':True}),encoding='utf-8')
work=Path(tempfile.mkdtemp(prefix='kb-live-',dir='D:/maiyi/tmp'));credentials=work/'synthetic-login.json'
credentials.write_text(json.dumps(login),encoding='utf-8')
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
tunnel=subprocess.Popen(['ssh',*opts,'-o','ExitOnForwardFailure=yes','-N','-L',f'127.0.0.1:{port}:127.0.0.1:19080','hermes-desktop-vps'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
status=1
try:
    for _ in range(50):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.5):break
        except OSError:time.sleep(.2)
    else:raise RuntimeError('SSH tunnel unavailable')
    gateway="""import asyncio,json,sys
from types import SimpleNamespace
from hermes_cli.plugins import get_plugin_manager,get_plugin_command_handler
manager=get_plugin_manager();manager.discover_and_load();handler=get_plugin_command_handler('wechat-knowledge-result');assert callable(handler)
module=sys.modules[handler.__module__];base,secret=module.connection();assert base and secret
source=SimpleNamespace(platform='qqbot',user_id=RECIPIENT,chat_type='dm')
result=module.pre_gateway_dispatch(event=SimpleNamespace(source=source,text='收藏笔记到我的知识库：Hermes 发布合成验收：只保存本人的来源和正文。'),gateway=None)
assert result and result['action']=='rewrite'
answer=asyncio.run(handler(result['text'].split(' ',1)[1]))
assert '资料编号' in answer or '已保存' in answer, 'Signed synthetic gateway note was not confirmed'
assert module.pre_gateway_dispatch(event=SimpleNamespace(source=source,text='知识库搜索：原生指令不拦截'),gateway=None) is None
print(json.dumps({'passed':True,'plugin_loaded':True,'signed_gateway_note':True,'native_command_preserved':True,'external_message_sent':False,'synthetic_only':True}))
""".replace('RECIPIENT',repr(login['synthetic_recipient']))
    g=subprocess.run([*ssh,'docker','exec','-i','-e','HERMES_HOME=/opt/data','hermes','/opt/hermes/.venv/bin/python','-'],input=gateway,capture_output=True,text=True,encoding='utf-8',timeout=50)
    safe=next((line for line in reversed(g.stdout.splitlines()) if line.startswith('{') and 'signed_gateway_note' in line),'')
    (base/'evidence/production-hermes-gateway.log').write_text(safe or json.dumps({'passed':False,'step':'synthetic Hermes hook'}),encoding='utf-8')
    if g.returncode:
        print(json.dumps({'passed':False,'step':'Hermes integration','safe_error':g.stderr.splitlines()[-1][:200] if g.stderr else ''}));raise SystemExit(g.returncode)
    with (base/'evidence/production-client-e2e.log').open('w',encoding='utf-8') as log:
        r=subprocess.run(['node','scripts/knowledge-production-e2e.mjs',str(credentials),f'http://127.0.0.1:{port}',str(base/'evidence/production-client-e2e'),str(root/'release/kb061-delivery/win-unpacked')],cwd=root,stdout=log,stderr=log,timeout=180)
    print(json.dumps({'desktop_code':r.returncode,'hermes_signed_gateway':g.returncode==0,'actual_production':True,'synthetic_only':True}));status=r.returncode
finally:
    tunnel.terminate();tunnel.wait(timeout=10);credentials.unlink(missing_ok=True)
    clean=subprocess.run([*ssh,'/opt/signaldesk-task/.venv/bin/python','-','cleanup','--marker',marker],input=code,capture_output=True,text=True,encoding='utf-8',timeout=40)
    (base/'evidence/production-synthetic-cleanup.log').write_text(clean.stdout.strip() or json.dumps({'cleaned':False,'step':'synthetic cleanup'}),encoding='utf-8')
    if clean.returncode:status=1
raise SystemExit(status)
