"""Read-only preflight as the exact future service user; print metadata only."""
import json, subprocess
from pathlib import Path
from src.main import load_config

config = load_config(Path('/opt/signaldesk/.env'))
health = next(item for item in config.recipients if item.key == 'health')
probe = r'''
import importlib,json,os,sys
values=json.loads(sys.stdin.read())
result={'uid':os.geteuid(),'gid':os.getegid(),'access':{},'imports':{},'network':{}}
for name,path,mode in [('collector_root','/opt/signaldesk',5),('collector_python','/opt/signaldesk/.venv/bin/python',5),('collector_env','/opt/signaldesk/.env',4),('collector_content','/opt/signaldesk/content/health',5),('task_env','/opt/signaldesk-task/.env',4),('health_config_parent','/etc/signaldesk',1)]:
 result['access'][name]={'exists':os.path.exists(path),'permitted':os.access(path,mode)}
for name in (['src.main','src.health_news_generator','src.content_loader','src.signaldesk_client'] if sys.argv[1]=='collector' else []):
 try:importlib.import_module(name);result['imports'][name]={'passed':True}
 except Exception as error:result['imports'][name]={'passed':False,'error_type':type(error).__name__}
try:import httpx
except ImportError:result['httpx_available']=False
else:result['httpx_available']=True
for trust in ([False,True] if result['httpx_available'] else []):
 try:
  with httpx.Client(timeout=12,trust_env=trust,follow_redirects=False) as client:
   response=client.get(values['server'].rstrip('/')+'/'+values['topic']+'/json',params={'poll':'1','since':'5m'})
   result['network'][str(trust)]={'status':response.status_code,'bytes':len(response.content)}
 except Exception as error:result['network'][str(trust)]={'error_type':type(error).__name__}
print('HEALTH_RUNTIME_RESULT='+json.dumps(result))
'''
all_results={}
for role,python in [('collector','/opt/signaldesk/.venv/bin/python'),('task','/opt/signaldesk-task/.venv/bin/python')]:
 result = subprocess.run(['/usr/sbin/runuser','-u','signaldesk-task','--',python,'-c',probe,role],cwd='/opt/signaldesk',input=json.dumps({'server':config.ntfy_server,'topic':health.topic}),text=True,capture_output=True,timeout=55)
 lines = [line for line in result.stdout.splitlines() if line.startswith('HEALTH_RUNTIME_RESULT=')]
 if result.returncode or not lines:
  all_results[role]={'passed':False,'phase':'service_runtime_preflight','exit_code':result.returncode,'diagnostic_output_retained':False}
 else:all_results[role]=json.loads(lines[-1].split('=',1)[1])
print(json.dumps(all_results))
