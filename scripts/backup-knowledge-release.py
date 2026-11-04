"""Server-private backup + restore/migration drill, with services restored on exit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--candidate',type=Path,required=True);p.add_argument('--test-env',type=Path,required=True)
p.add_argument('--keep-paused',action='store_true',help='Only for an immediately following authorized publish; failures always resume')
args=p.parse_args();os.umask(0o077)
app=Path('/opt/signaldesk-task');candidate=args.candidate.resolve()
if candidate.parent!=Path('/var/tmp') or not candidate.name.startswith('signaldesk-knowledge-candidate-'):raise SystemExit('Invalid candidate')
if args.test_env.parent.parent!=Path('/var/tmp') or not args.test_env.parent.name.startswith('signaldesk-kb-mysql-'):raise SystemExit('Private test environment required')
manifest=json.loads((candidate/'knowledge-release-manifest.json').read_text())
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
for row in manifest['files']:
    assert sha(app/row['path'])==row['live_sha256'],'Production baseline changed'
    assert sha(candidate/row['path'])==row['candidate_sha256'],'Candidate changed'
backup=Path('/var/backups/signaldesk-task')/('knowledge-061-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+secrets.token_hex(3))
backup.mkdir(parents=True,mode=0o700)
def emit(phase,**data):print(json.dumps({'phase':phase,**data}),flush=True)
def run(argv,**kw):
    r=subprocess.run([str(v) for v in argv],capture_output=True,text=True,timeout=kw.pop('timeout',90),**kw)
    if r.returncode:
        with (backup/'commands.private.log').open('a') as f:f.write(r.stdout+'\n'+r.stderr+'\n')
        raise RuntimeError('Server-local command failed')
    return r.stdout
units=['signaldesk-task-api','signaldesk-task-worker','signaldesk-knowledge-worker']
active={u:subprocess.run(['systemctl','is-active',u],capture_output=True,text=True).stdout.strip()=='active' for u in units}
metadata={}
def remember(key,path):
    if path.exists():
        st=path.stat();metadata[key]={'st_mode':st.st_mode,'st_uid':st.st_uid,'st_gid':st.st_gid}
for folder in ['app','migrations']:
    for file in (app/folder).rglob('*'):
        if file.is_file() and '__pycache__' not in file.parts:remember('app/'+file.relative_to(app).as_posix(),file)
    shutil.copytree(app/folder,backup/'originals'/folder,ignore=shutil.ignore_patterns('__pycache__'))
for name in ['.env','alembic.ini','requirements.txt']:
    remember('app/'+name,app/name);shutil.copy2(app/name,backup/'originals'/name)
home=Path('/home/hermes/.hermes')
for name in ['config.yaml','signaldesk-knowledge.env']:
    if (home/name).exists():remember('home/'+name,home/name);shutil.copy2(home/name,backup/name)
if (home/'plugins/wechat-knowledge').exists():
    for file in (home/'plugins/wechat-knowledge').glob('*'):
        if file.is_file():remember('plugin/'+file.name,file)
    shutil.copytree(home/'plugins/wechat-knowledge',backup/'hermes-plugin',ignore=shutil.ignore_patterns('__pycache__'))
for unit in ['signaldesk-knowledge-worker.service','signaldesk-knowledge-dispatch.service']:
    src=Path('/etc/systemd/system')/unit
    if src.exists():remember('unit/'+unit,src);shutil.copy2(src,backup/unit)
(backup/'file-metadata.json').write_text(json.dumps(metadata))
(backup/'service-state.json').write_text(json.dumps(active))
os.chdir(app);sys.path.insert(0,str(app))
from app.config import get_settings
settings=get_settings();live_url=make_url(settings.database_url)
assert live_url.get_backend_name()=='mysql'
live=create_engine(live_url)
def snapshot(engine):
    tables=inspect(engine).get_table_names()
    with engine.connect() as c:
        return {'version':c.execute(text('SELECT version_num FROM alembic_version')).scalar(),
                'counts':{t:c.execute(text('SELECT COUNT(*) FROM '+engine.dialect.identifier_preparer.quote(t))).scalar() for t in tables}}
def client_file(url,target):
    values={'user':url.username,'password':url.password or '','host':url.host or 'localhost','port':str(url.port or 3306)}
    if url.query.get('unix_socket'):values['socket']=url.query['unix_socket']
    target.write_text('[client]\n'+''.join(k+'='+json.dumps(v)+'\n' for k,v in values.items()));target.chmod(0o600)
    return target
test_url=make_url(dotenv_values(args.test_env)['KNOWLEDGE_TEST_DATABASE_URL'])
admin=create_engine(URL.create(test_url.drivername,username=test_url.username,password=test_url.password,host=test_url.host,port=test_url.port,query=test_url.query))
schema='signaldesk_kb_test_restore_'+secrets.token_hex(6);created=False;shadow=None;completed=False
try:
    emit('pausing_writes',backup=str(backup))
    run(['systemctl','stop',units[0],units[1]])
    for _ in range(90):
        with live.connect() as c:busy=c.execute(text("SELECT COUNT(*) FROM knowledge_job WHERE state='running'")).scalar()
        if not busy:break
        time.sleep(1)
    assert not busy,'Recognition still running; avoid interrupting a paid job'
    run(['systemctl','stop',units[2]])
    before=snapshot(live);assert before['version'] in {'20261008_03','20261008_12'}
    (backup/'before.json').write_text(json.dumps(before))
    cf=client_file(live_url,backup/'dump-client.private.cnf')
    with (backup/'database.sql').open('xb') as out,(backup/'dump.private.log').open('wb') as err:
        r=subprocess.run(['/usr/bin/mysqldump','--defaults-extra-file='+str(cf),'--single-transaction','--quick','--hex-blob','--no-tablespaces','--set-gtid-purged=OFF','--skip-comments',live_url.database],stdout=out,stderr=err,timeout=120)
    cf.unlink();assert r.returncode==0 and (backup/'database.sql').stat().st_size>100,'Database backup failed'
    storage=Path(settings.knowledge_storage_dir).resolve()
    assert storage!=Path('/') and str(storage).startswith('/var/lib/signaldesk-knowledge')
    with tarfile.open(backup/'original-files.tar.gz','w:gz') as tar:
        if storage.exists():tar.add(storage,arcname='knowledge-originals')
    emit('backup_ready',tables=len(before['counts']))
    with live.connect() as c:
        charset,collation=c.execute(text('SELECT DEFAULT_CHARACTER_SET_NAME,DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=DATABASE()')).one()
    assert re.fullmatch(r'[a-zA-Z0-9_]+',charset) and re.fullmatch(r'[a-zA-Z0-9_]+',collation)
    with admin.begin() as c:c.exec_driver_sql('CREATE DATABASE `'+schema+'` CHARACTER SET '+charset+' COLLATE '+collation)
    created=True;restore_url=test_url.set(database=schema)
    cf=client_file(restore_url,backup/'restore-client.private.cnf')
    with (backup/'database.sql').open('rb') as source,(backup/'restore.private.log').open('wb') as err:
        r=subprocess.run(['/usr/bin/mysql','--defaults-extra-file='+str(cf),'--binary-mode','--default-character-set=utf8mb4',schema],stdin=source,stdout=err,stderr=err,timeout=120)
    cf.unlink();assert r.returncode==0,'Backup restore failed'
    shadow=create_engine(restore_url);restored=snapshot(shadow);assert restored==before,'Restored data counts differ'
    env={**os.environ,'TASK_DATABASE_URL':restore_url.render_as_string(hide_password=False),'TASK_API_KEY':'synthetic-migration-acceptance','PYTHONDONTWRITEBYTECODE':'1'}
    for _ in range(2):run(['/opt/signaldesk-task/.venv/bin/python','-m','alembic','upgrade','20261008_12'],cwd=candidate,env=env,timeout=90)
    after=snapshot(shadow);assert after['version']=='20261008_12'
    assert all(after['counts'][t]==n for t,n in before['counts'].items() if t!='alembic_version')
    result={'passed':True,'backup':str(backup),'database_sha256':sha(backup/'database.sql'),'originals_sha256':sha(backup/'original-files.tar.gz'),
            'restored_mysql_version':'8.0.43','restored_tables':len(before['counts']),'migration':before['version']+' -> 20261008_12 -> 20261008_12',
            'existing_counts_preserved':True,'candidate':str(candidate),'production_code_changed':False}
    (backup/'validated.json').write_text(json.dumps(result,indent=2));completed=True;emit('restore_and_migration_passed',**result)
except Exception as error:
    (backup/'failure.private.log').write_text(traceback.format_exc());emit('failed',error_type=type(error).__name__,backup=str(backup));raise SystemExit(1)
finally:
    if shadow is not None:shadow.dispose()
    if created:
        with admin.begin() as c:c.exec_driver_sql('DROP DATABASE `'+schema+'`')
    admin.dispose();live.dispose()
    if not (args.keep_paused and completed):
        for u,on in active.items():
            if on:subprocess.run(['systemctl','start',u],capture_output=True,timeout=90)
        emit('original_services_resumed')
    else:emit('services_paused_for_publish',backup=str(backup))
