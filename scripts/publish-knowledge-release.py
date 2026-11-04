"""Authorized 0.6.1 knowledge-only publish, after private MySQL restore rehearsal."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace
from urllib.parse import urlsplit
import zipfile
from dotenv import dotenv_values
import httpx
from sqlalchemy import create_engine,text

p=argparse.ArgumentParser(description=__doc__)
for name in ['candidate','test-env','backup-script','plugin-archive']:p.add_argument('--'+name,type=Path,required=True)
p.add_argument('--plugin-sha',required=True)
args=p.parse_args();os.umask(0o077)
APP=Path('/opt/signaldesk-task');HOME=Path('/home/hermes/.hermes');PY=APP/'.venv/bin/python'
candidate=args.candidate.resolve();payload=candidate.with_name(candidate.name.replace('signaldesk-knowledge-candidate-','signaldesk-knowledge-final-'))
assert candidate.parent==Path('/var/tmp') and candidate.name.startswith('signaldesk-knowledge-candidate-')
manifest=json.loads((candidate/'knowledge-release-manifest.json').read_text())
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
assert sha(args.plugin_archive)==args.plugin_sha
for row in manifest['files']:
    assert sha(APP/row['path'])==row['live_sha256'],'Live baseline changed'
    assert sha(candidate/row['path'])==row['candidate_sha256'],'Candidate changed'
qwen=dotenv_values(HOME/'.env').get('DASHSCOPE_API_KEY');assert qwen and '\n' not in qwen and '\r' not in qwen
live_values=dotenv_values(APP/'.env');webhook=urlsplit(live_values.get('TASK_HERMES_WEBHOOK_URL',''))
gateway_secret=live_values.get('TASK_KNOWLEDGE_GATEWAY_SECRET') or secrets.token_urlsafe(48)
gateway_base=dotenv_values(HOME/'.env').get('TASK_API_BASE_URL','').rstrip('/')
assert urlsplit(gateway_base).scheme in {'http','https'} and urlsplit(gateway_base).hostname
assert webhook.scheme=='http' and webhook.hostname=='127.0.0.1' and webhook.port==18644
webhook_base='http://127.0.0.1:18644';notification_secret=secrets.token_urlsafe(48)
plugin_files={}
with zipfile.ZipFile(args.plugin_archive) as z:
    assert z.testzip() is None
    for name in ['__init__.py','plugin.yaml','notification-routes.example.json','README.md']:
        plugin_files[name]=z.read('hermes-plugin/wechat-knowledge/'+name)
route_name='signaldesk-knowledge-qq';config_candidate=HOME/('knowledge-release-config-'+secrets.token_hex(5)+'.yaml')
prepare_config='''import json,os,yaml
from pathlib import Path
path=Path('/opt/data/config.yaml');cfg=yaml.safe_load(path.read_text());platforms=cfg.get('platforms',{})
assert platforms.get('webhook',{}).get('enabled') and platforms.get('qqbot',{}).get('enabled')
extra=platforms['webhook'].setdefault('extra',{});routes=extra.setdefault('routes',{})
assert isinstance(routes,dict)
route={'secret':SECRET,'events':['knowledge.finished','knowledge.alert'],'deliver':'qqbot','deliver_only':True,'deliver_extra':{'chat_id':'{recipient_id}'},'prompt':'{message}'}
if 'signaldesk-knowledge-qq' in routes:assert routes['signaldesk-knowledge-qq'].get('deliver_only') is True
routes['signaldesk-knowledge-qq']=route
plugins=cfg.setdefault('plugins',{});enabled=plugins.setdefault('enabled',[]);disabled=plugins.setdefault('disabled',[])
assert isinstance(enabled,list) and isinstance(disabled,list)
if 'wechat-knowledge' not in enabled:enabled.append('wechat-knowledge')
if 'wechat-knowledge' in disabled:disabled.remove('wechat-knowledge')
out=Path(OUTPUT);out.write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False));out.chmod(0o600)
st=path.stat();os.chown(out,st.st_uid,st.st_gid)
print(json.dumps({'prepared':True,'route':'signaldesk-knowledge-qq','direct_only':True,'other_platforms_preserved':True}))
'''.replace('SECRET',repr(notification_secret)).replace('OUTPUT',repr('/opt/data/'+config_candidate.name))
backup=None;changed=False;hermes_changed=False
units=['signaldesk-task-api','signaldesk-task-worker','signaldesk-knowledge-worker','signaldesk-knowledge-dispatch']
original_active={u:subprocess.run(['systemctl','is-active',u],capture_output=True,text=True).stdout.strip()=='active' for u in units}
def emit(phase,**value):print(json.dumps({'phase':phase,**value}),flush=True)
def run(argv,**kw):
    r=subprocess.run([str(v) for v in argv],capture_output=True,text=True,timeout=kw.pop('timeout',90),**kw)
    if r.returncode:
        if backup:
            with (backup/'publish.private.log').open('a') as f:f.write(r.stdout+'\n'+r.stderr+'\n')
        raise RuntimeError('Publish command failed; server-private diagnostics retained')
    return r.stdout
def atomic_bytes(data,target,metadata):
    temp=target.with_name(target.name+'.kb061-'+secrets.token_hex(4));temp.write_bytes(data)
    os.chmod(temp,stat.S_IMODE(metadata.st_mode));os.chown(temp,metadata.st_uid,metadata.st_gid);os.replace(temp,target)
def restore_old():
    if not backup:return
    original_metadata=json.loads((backup/'file-metadata.json').read_text())
    def meta(key,fallback):return SimpleNamespace(**original_metadata[key]) if key in original_metadata else fallback.stat()
    subprocess.run(['systemctl','stop',*units],capture_output=True,timeout=90)
    if changed:
        for row in manifest['files']:
            dest=APP/row['path'];old=backup/'originals'/row['path']
            if old.exists():atomic_bytes(old.read_bytes(),dest,meta('app/'+row['path'],old))
            elif dest.exists():
                retained=backup/'retained-added-code'/row['path'];retained.parent.mkdir(parents=True,exist_ok=True);os.replace(dest,retained)
        old=backup/'originals/.env';atomic_bytes(old.read_bytes(),APP/'.env',meta('app/.env',old))
        for unit in ['signaldesk-knowledge-worker.service','signaldesk-knowledge-dispatch.service']:
            target=Path('/etc/systemd/system')/unit;old=backup/unit
            if old.exists():atomic_bytes(old.read_bytes(),target,meta('unit/'+unit,old))
            elif target.exists():
                subprocess.run(['systemctl','disable',unit],capture_output=True);os.replace(target,backup/('retained-'+unit))
    if hermes_changed:
        old=backup/'config.yaml';atomic_bytes(old.read_bytes(),HOME/'config.yaml',meta('home/config.yaml',old))
        old=backup/'signaldesk-knowledge.env';dest=HOME/'signaldesk-knowledge.env'
        if old.exists():atomic_bytes(old.read_bytes(),dest,meta('home/signaldesk-knowledge.env',old))
        elif dest.exists():os.replace(dest,backup/'retained-gateway.env')
        for name in plugin_files:
            old=backup/'hermes-plugin'/name;dest=HOME/'plugins/wechat-knowledge'/name
            if old.exists():atomic_bytes(old.read_bytes(),dest,meta('plugin/'+name,old))
            elif dest.exists():os.replace(dest,backup/('retained-plugin-'+name))
        subprocess.run(['docker','restart','--time','20','hermes'],capture_output=True,timeout=90)
    subprocess.run(['systemctl','daemon-reload'],capture_output=True,timeout=30)
try:
    run(['docker','exec','-i','hermes','/opt/hermes/.venv/bin/python','-'],input=prepare_config,timeout=30)
    emit('configuration_prepared',other_platforms_preserved=True)
    rehearsal=subprocess.run([str(PY),str(args.backup_script),'--candidate',str(candidate),'--test-env',str(args.test_env),'--keep-paused'],capture_output=True,text=True,timeout=340)
    for line in rehearsal.stdout.splitlines():
        value=json.loads(line);emit('backup_rehearsal',result=value)
        if value.get('phase')=='restore_and_migration_passed':backup=Path(value['backup'])
    assert rehearsal.returncode==0 and backup and (backup/'validated.json').exists(),'Fresh backup rehearsal failed'
    before=json.loads((backup/'before.json').read_text());live_before=sha(APP/'.env')
    assert live_before==sha(backup/'originals/.env'),'Environment changed during backup'
    for row in manifest['files']:assert sha(APP/row['path'])==row['live_sha256']
    changed=True;metadata=(APP/'app/main.py').stat()
    for row in manifest['files']:
        dest=APP/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
        atomic_bytes((candidate/row['path']).read_bytes(),dest,dest.stat() if dest.exists() else metadata)
    updates={'TASK_KNOWLEDGE_CLOUD_ASR_PROVIDER':'qwen','TASK_KNOWLEDGE_CLOUD_ASR_MODEL':'qwen3-asr-flash','TASK_KNOWLEDGE_CLOUD_ASR_ENABLED':'true',
        'TASK_KNOWLEDGE_QWEN_API_KEY':qwen,'TASK_KNOWLEDGE_QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'TASK_KNOWLEDGE_EMBEDDING_PROVIDER':'qwen','TASK_KNOWLEDGE_EMBEDDING_MODEL':'text-embedding-v4','TASK_KNOWLEDGE_SEMANTIC_ENABLED':'true',
        'TASK_KNOWLEDGE_VIDEO_OCR_ENABLED':'true','TASK_KNOWLEDGE_ORGANIZATION_ENABLED':'true','TASK_KNOWLEDGE_CLOUD_QA_ENABLED':'true',
        'TASK_KNOWLEDGE_DISPATCH_ENABLED':'true','TASK_KNOWLEDGE_NOTIFICATION_WEBHOOK_BASE':webhook_base,'TASK_KNOWLEDGE_NOTIFICATION_WEBHOOK_SECRET':notification_secret,
        'TASK_KNOWLEDGE_GATEWAY_SECRET':gateway_secret}
    lines=[line for line in (APP/'.env').read_text().splitlines() if line.partition('=')[0].strip() not in updates]
    env_text='\n'.join(lines)+'\n'+''.join(k+'='+json.dumps(v)+'\n' for k,v in updates.items())
    atomic_bytes(env_text.encode(),APP/'.env',(APP/'.env').stat())
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    run([PY,'-m','alembic','upgrade','20261008_12'],cwd=APP,env=env)
    os.chdir(APP);sys.path.insert(0,str(APP))
    from app.config import get_settings
    settings=get_settings();engine=create_engine(settings.database_url)
    with engine.connect() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261008_12'
        assert all(c.execute(text('SELECT COUNT(*) FROM '+engine.dialect.identifier_preparer.quote(t))).scalar()==n for t,n in before['counts'].items() if t!='alembic_version')
    engine.dispose();emit('production_migrated',schema='20261008_12',existing_counts_preserved=True)
    for unit in ['signaldesk-knowledge-worker.service','signaldesk-knowledge-dispatch.service']:
        target=Path('/etc/systemd/system')/unit;src=payload/'server/deploy'/unit
        assert src.is_file();target.write_bytes(src.read_bytes());target.chmod(0o644)
    hermes_changed=True
    plugin=HOME/'plugins/wechat-knowledge';plugin.mkdir(exist_ok=True);plugin.chmod(0o755)
    home_stat=(HOME/'config.yaml').stat();os.chown(plugin,home_stat.st_uid,home_stat.st_gid)
    plugin_meta=SimpleNamespace(st_mode=0o644,st_uid=home_stat.st_uid,st_gid=home_stat.st_gid)
    for name,data in plugin_files.items():atomic_bytes(data,plugin/name,plugin_meta)
    gateway_text='TASK_KNOWLEDGE_API_BASE_URL='+gateway_base+'\nTASK_KNOWLEDGE_GATEWAY_SECRET='+gateway_secret+'\n'
    atomic_bytes(gateway_text.encode(),HOME/'signaldesk-knowledge.env',SimpleNamespace(st_mode=0o600,st_uid=home_stat.st_uid,st_gid=home_stat.st_gid))
    atomic_bytes(config_candidate.read_bytes(),HOME/'config.yaml',(HOME/'config.yaml').stat())
    run(['systemctl','daemon-reload']);run(['systemctl','start',units[0]])
    with httpx.Client(timeout=5,trust_env=False) as client:
        for _ in range(30):
            try:
                if client.get('http://127.0.0.1:19080/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(1)
        else:raise RuntimeError('API health timeout')
        assert client.get('http://127.0.0.1:19080/api/v1/knowledge/capabilities').status_code==401
        assert client.get('http://127.0.0.1:19080/api/v1/admin/members',headers={'X-SignalDesk-Key':settings.api_key}).status_code==200
    run(['systemctl','enable','--now',units[2],units[3]])
    run(['docker','restart','--time','20','hermes'],timeout=90)
    run(['systemctl','start',units[1]])
    for u in units:assert run(['systemctl','is-active',u]).strip()=='active'
    from app.knowledge_qwen import configured
    from app.knowledge_notifications import configuration
    assert configured() and configuration() and settings.knowledge_cloud_asr_provider=='qwen' and settings.knowledge_semantic_enabled
    result={'passed':True,'backup':str(backup),'schema':'20261008_12','candidate':str(candidate),
        'services':{u:'active' for u in units},'qwen_asr':'qwen3-asr-flash','qwen_embedding':'text-embedding-v4',
        'notification_route':'qqbot deliver_only','existing_data_preserved':True,'real_message_sent':False,
        'files':{row['path']:sha(APP/row['path']) for row in manifest['files']},'hermess2_changed':False}
    (backup/'published.json').write_text(json.dumps(result,indent=2));emit('published',**result)
except Exception as error:
    if backup:(backup/'publish-failure.private.log').write_text(traceback.format_exc())
    restore_old()
    for unit,on in original_active.items():
        if on:subprocess.run(['systemctl','start',unit],capture_output=True,timeout=90)
    emit('publish_failed_old_services_resumed',error_type=type(error).__name__,backup=str(backup) if backup else None,additive_schema_retained=True)
    raise SystemExit(1)
finally:
    if config_candidate.exists():config_candidate.unlink()
