"""Server-local, incremental personal KB deployment; never export credentials/data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import sys
import tarfile
import time

APP = Path('/opt/signaldesk-task')
HOME = Path('/home/hermes/.hermes')
STAGE = Path('/var/tmp/signaldesk-knowledge-20261008')
PYTHON = APP / '.venv/bin/python'
API, TASK = 'signaldesk-task-api.service', 'signaldesk-task-worker.service'
KB = 'signaldesk-knowledge-worker.service'
UNIT = Path('/etc/systemd/system') / KB
PLUGIN = HOME / 'plugins/wechat-knowledge'
SECRET_FILE = HOME / 'signaldesk-knowledge.env'
NEW_FILES = ['app/' + name + '.py' for name in [
    'knowledge', 'knowledge_cleanup', 'knowledge_cloud', 'knowledge_extract',
    'knowledge_extract_cli', 'knowledge_gateway', 'knowledge_models',
    'knowledge_ocr', 'knowledge_service', 'knowledge_worker',
]] + ['migrations/versions/' + name + '.py' for name in [
    '20261008_01_knowledge', '20261008_02_knowledge_cleanup', '20261008_03_knowledge_ocr',
]]
PATCH_FILES = ['app/main.py', 'app/config.py', 'migrations/env.py', 'requirements.txt']
BASE = {
    'app/main.py': 'de8181770aa0a73598acf11659dd92f4fdfca0f233bb42b347f4c2820fcfe72a',
    'app/config.py': 'e08e6650a8be5b66640b5f5af24b206170137ff7522e0bc0c64af5c23047b01f',
    'migrations/env.py': 'f3ffe0198e64540415b444f81be8b6f9c4efcf48c69f7219cadef4ee2fb1ce66',
    'requirements.txt': '1907555ca2adf49daf7eda7fdff98441224ab8d7da020fc584b1ec041a7f1689',
}
COUNTS = ['staff', 'user_account', 'task', 'task_event', 'content_account', 'content_episode', 'notification_outbox']

def emit(phase, **values):
    print(json.dumps(dict(phase=phase, **values), ensure_ascii=False), flush=True)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)

def run(args, *, cwd=APP, env=None, input=None, timeout=60):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, input=input,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        with (STAGE / 'commands.private.log').open('a') as log:
            log.write(f'{Path(args[0]).name} rc={result.returncode}\n{result.stdout}\n{result.stderr}\n')
        raise RuntimeError('Command failed; see server-local private log')
    return result.stdout

def snapshot():
    from app.config import get_settings
    from sqlalchemy import create_engine, text
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        result = dict(revision=conn.execute(text('SELECT version_num FROM alembic_version')).scalar(),
                      counts={t: conn.execute(text('SELECT COUNT(*) FROM ' + t)).scalar() for t in COUNTS})
    engine.dispose()
    return result

def baseline():
    assert all(sha(APP / name) == digest for name, digest in BASE.items()), 'Production baseline changed'
    assert all(not (APP / name).exists() for name in NEW_FILES), 'Knowledge files already exist'
    assert not any(p.exists() for p in [PLUGIN, SECRET_FILE, UNIT, Path('/var/lib/signaldesk-knowledge')]), 'Deployment targets already exist'
    assert snapshot()['revision'] == '20260928_01', 'Migration baseline changed'

def replace_once(body, old, new):
    assert body.count(old) == 1, 'Patch anchor is not unique'
    return body.replace(old, new)

def prepare():
    baseline()
    archive = STAGE / 'payload.tar.gz'
    assert sha(archive) == ARGS.payload_sha, 'Payload checksum mismatch'
    allowed = {'server/' + p for p in NEW_FILES} | {'server/app/config.py',
        'hermes-plugin/wechat-knowledge/__init__.py', 'hermes-plugin/wechat-knowledge/plugin.yaml'}
    payload = STAGE / 'payload'
    payload.mkdir(exist_ok=True)
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        assert {m.name for m in members} == allowed and all(m.isfile() for m in members), 'Unsafe payload'
        for member in members:
            target = payload / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
    candidate = STAGE / 'candidate'
    shutil.copytree(APP / 'app', candidate / 'app', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(APP / 'migrations', candidate / 'migrations', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    for name in NEW_FILES:
        shutil.copyfile(payload / 'server' / name, candidate / name)
    main = (APP / 'app/main.py').read_text()
    main = replace_once(main, 'from .members import router as members_router',
        'from .members import router as members_router\nfrom .knowledge import router as knowledge_router\nfrom .knowledge_gateway import router as knowledge_gateway_router')
    main = replace_once(main, 'app.include_router(members_router)',
        'app.include_router(members_router)\napp.include_router(knowledge_router)\napp.include_router(knowledge_gateway_router)')
    (candidate / 'app/main.py').write_text(main)
    config = (APP / 'app/config.py').read_text()
    source = (payload / 'server/app/config.py').read_text()
    start, end = source.index('    knowledge_storage_dir:'), source.index('    reminder_poll_seconds:')
    config = replace_once(config, '    model_config = SettingsConfigDict(', source[start:end] + '\n    model_config = SettingsConfigDict(')
    (candidate / 'app/config.py').write_text(config)
    migration = replace_once((APP / 'migrations/env.py').read_text(), 'from app import content_models',
        'from app import knowledge_models  # noqa: F401\nfrom app import content_models')
    (candidate / 'migrations/env.py').write_text(migration)
    (candidate / 'requirements.txt').write_text((APP / 'requirements.txt').read_text().rstrip() + '\npypdf==6.18.1\n')
    # Install only the additive parser dependency, not the entire local lockfile.
    run([PYTHON, '-m', 'pip', 'install', '--no-deps', 'pypdf==6.18.1'], timeout=120)
    for path in (candidate / 'app').glob('*.py'):
        compile(path.read_text(), str(path), 'exec')
    env = {**os.environ, 'TASK_DATABASE_URL': 'sqlite://', 'TASK_API_KEY': 'synthetic-deployment-probe',
           'TASK_KNOWLEDGE_OCR_ENABLED': 'false', 'TASK_HERMES_WEBHOOK_URL': '', 'PYTHONDONTWRITEBYTECODE': '1'}
    run([PYTHON, '-c', "import app.main,app.knowledge_worker; assert any(r.path=='/api/v1/knowledge/gateway' for r in app.main.app.routes)"], cwd=candidate, env=env)
    config_body = (HOME / 'config.yaml').read_text()
    def validate_yaml(body, new):
        code = "import yaml,sys; p=yaml.safe_load(sys.stdin.read())['plugins']['enabled']; assert 'knowledge-base' in p; assert ('wechat-knowledge' in p)==" + repr(new)
        run(['docker','exec','-i','hermes','/opt/hermes/.venv/bin/python','-c',code], input=body)
    validate_yaml(config_body, False)
    # Patch only the enabled plugin list; retain all other config formatting/comments.
    section = config_body.index('\nplugins:\n')
    before, tail = config_body[:section], config_body[section:]
    tail = replace_once(tail, '  disabled: []', '    - wechat-knowledge\n  disabled: []')
    (STAGE / 'hermes-config.yaml').write_text(before + tail)
    validate_yaml(before + tail, True)
    save(STAGE / 'prepared.json', dict(env_sha=sha(APP / '.env'), hermes_config_sha=sha(HOME / 'config.yaml'),
         candidates={p: sha(candidate / p) for p in NEW_FILES + PATCH_FILES}))
    emit('prepared', candidate_imports=True, additive_dependency='pypdf 6.18.1', new_modules=len(NEW_FILES))

def atomic(source, target, info):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.' + target.name + '.knowledge-tmp')
    with temporary.open('xb') as out:
        out.write(source.read_bytes()); out.flush(); os.fsync(out.fileno())
    os.chmod(temporary, info.st_mode & 0o777)
    os.chown(temporary, info.st_uid, info.st_gid)
    os.replace(temporary, target)

def health():
    import urllib.request
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:19080/health', timeout=2) as response:
                if json.loads(response.read()) == {'status': 'ok'}: return
        except Exception: time.sleep(1)
    raise RuntimeError('API health check failed')

def deploy():
    baseline()
    prepared = json.loads((STAGE / 'prepared.json').read_text())
    assert sha(APP / '.env') == prepared['env_sha'] and sha(HOME / 'config.yaml') == prepared['hermes_config_sha']
    assert all(sha(STAGE / 'candidate' / p) == h for p, h in prepared['candidates'].items())
    from dotenv import dotenv_values
    from urllib.parse import urlsplit
    values = dotenv_values(HOME / '.env')
    key, base = values.get('ARK_API_KEY'), (values.get('TASK_API_BASE_URL') or '').rstrip('/')
    assert key and '\n' not in key and '\r' not in key and urlsplit(base).scheme in {'http','https'} and urlsplit(base).hostname
    backup = Path('/var/backups/signaldesk-task') / time.strftime('knowledge-%Y%m%dT%H%M%SZ', time.gmtime())
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    originals = backup / 'originals'
    for name in PATCH_FILES + ['.env']:
        path = originals / name; path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(APP / name, path)
    shutil.copy2(HOME / 'config.yaml', backup / 'hermes-config.yaml')
    with tarfile.open(backup / 'code-and-config.tar.gz', 'w:gz') as tar:
        for name in ['app','migrations','alembic.ini','requirements.txt','.env']:
            tar.add(APP / name, arcname=name, filter=lambda m: None if '__pycache__' in m.name else m)
    emit('code_backup_ready', backup=str(backup))
    changed = False
    try:
        run(['systemctl','stop',TASK,API])
        before = snapshot()
        save(backup / 'before.json', before)
        from app.config import get_settings
        from sqlalchemy.engine import make_url
        url = make_url(get_settings().database_url)
        assert url.get_backend_name() == 'mysql'
        args = ['mysqldump','--single-transaction','--quick','--hex-blob','--no-tablespaces','--set-gtid-purged=OFF',
                '--triggers','--routines','--events','--host='+url.host,'--port='+str(url.port or 3306),
                '--user='+url.username,'--databases',url.database]
        with (backup / 'database.sql').open('xb') as out, (backup / 'dump.private.log').open('xb') as errors:
            result = subprocess.run(args, stdout=out, stderr=errors, env={**os.environ,'MYSQL_PWD':url.password or ''}, timeout=120)
        assert result.returncode == 0 and (backup / 'database.sql').stat().st_size > 100, 'Database backup failed'
        emit('database_backup_ready')
        changed = True
        info = (APP / 'app/main.py').stat()
        for name in NEW_FILES + PATCH_FILES:
            atomic(STAGE / 'candidate' / name, APP / name, (APP/name).stat() if (APP/name).exists() else info)
        account = pwd.getpwnam('signaldesk-task')
        storage = Path('/var/lib/signaldesk-knowledge')
        storage.mkdir(mode=0o700, exist_ok=False)
        os.chown(storage, account.pw_uid, account.pw_gid)
        secret = secrets.token_urlsafe(48)
        env_body = (APP / '.env').read_text().rstrip() + '\n' + '\n'.join([
            'TASK_KNOWLEDGE_STORAGE_DIR='+str(storage), 'TASK_KNOWLEDGE_GATEWAY_SECRET='+secret,
            'TASK_KNOWLEDGE_OCR_ENABLED=true','TASK_KNOWLEDGE_OCR_API_KEY='+json.dumps(key),
            'TASK_KNOWLEDGE_OCR_MODEL=doubao-seed-2-0-lite-260428','TASK_KNOWLEDGE_OCR_MAX_PDF_PAGES=10',
            'TASK_KNOWLEDGE_ASR_ENABLED=false','TASK_KNOWLEDGE_CLOUD_QA_ENABLED=false','TASK_KNOWLEDGE_TRASH_RETENTION_DAYS=0']) + '\n'
        staged_env = STAGE / 'backend.env'; staged_env.write_text(env_body); staged_env.chmod(0o600)
        atomic(staged_env, APP / '.env', (APP / '.env').stat())
        SECRET_FILE.write_text('TASK_KNOWLEDGE_API_BASE_URL='+base+'\nTASK_KNOWLEDGE_GATEWAY_SECRET='+secret+'\n')
        SECRET_FILE.chmod(0o600)
        envinfo = (HOME / '.env').stat(); os.chown(SECRET_FILE, envinfo.st_uid, envinfo.st_gid)
        PLUGIN.mkdir(mode=0o755)
        for name in ['__init__.py','plugin.yaml']:
            atomic(STAGE / 'payload/hermes-plugin/wechat-knowledge' / name, PLUGIN / name, info)
        atomic(STAGE / 'hermes-config.yaml', HOME / 'config.yaml', (HOME / 'config.yaml').stat())
        UNIT.write_text('[Unit]\nDescription=SignalDesk personal knowledge worker\nAfter=network.target\n\n[Service]\nUser=signaldesk-task\nGroup=signaldesk-task\nWorkingDirectory=/opt/signaldesk-task\nEnvironmentFile=/opt/signaldesk-task/.env\nExecStart=/opt/signaldesk-task/.venv/bin/python -m app.knowledge_worker\nRestart=on-failure\nRestartSec=5\nUMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\nProtectHome=true\n\n[Install]\nWantedBy=multi-user.target\n')
        UNIT.chmod(0o644)
        run([PYTHON,'-m','alembic','upgrade','head'])
        after = snapshot(); assert after['revision'] == '20261008_03' and after['counts'] == before['counts']
        emit('migrated', revision=after['revision'], existing_record_counts_unchanged=True)
        run(['systemctl','daemon-reload']); run(['systemctl','start',API]); health()
        run(['systemctl','enable','--now',KB])
        run(['docker','restart','--time','20','hermes'])
        run(['systemctl','start',TASK])
        save(STAGE / 'deployed.json', dict(backup=str(backup)))
        verify()
        emit('deployed', backup=str(backup))
    except Exception:
        if changed:
            run(['systemctl','stop',TASK,API])
            if UNIT.exists():
                run(['systemctl','stop',KB])
                run(['systemctl','disable',KB])
            for name in PATCH_FILES + ['.env']:
                atomic(originals/name, APP/name, (originals/name).stat())
            for name in NEW_FILES:
                target=APP/name
                if target.exists():
                    retained=backup/'retained-new-code'/name; retained.parent.mkdir(parents=True,exist_ok=True)
                    shutil.move(str(target),str(retained))
            atomic(backup/'hermes-config.yaml',HOME/'config.yaml',(backup/'hermes-config.yaml').stat())
            for target in [PLUGIN,SECRET_FILE,UNIT]:
                if target.exists(): shutil.move(str(target),str(backup/target.name))
            run(['systemctl','daemon-reload']); run(['docker','restart','--time','20','hermes'])
        run(['systemctl','start',API,TASK]); health()
        emit('rolled_back', backup=str(backup), additive_schema_and_original_files='retained')
        raise

def verify():
    health()
    assert snapshot()['revision'] == '20261008_03'
    for unit in [API,TASK,KB]: assert run(['systemctl','is-active',unit]).strip() == 'active'
    assert json.loads(run(['docker','inspect','--format','{{json .State}}','hermes']))['Running']
    probe = """
from app.config import get_settings
from app.knowledge_ocr import configuration
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import json
s=get_settings(); assert configuration()
for path in ['/api/v1/knowledge/capabilities','/api/v1/knowledge/gateway']:
    request=Request('http://127.0.0.1:19080'+path, data=b'{}' if path.endswith('gateway') else None, headers={'Content-Type':'application/json'})
    try: urlopen(request,timeout=5); raise AssertionError('Unauthenticated access')
    except HTTPError as e: assert e.code in [401,503],e.code
request=Request('http://127.0.0.1:19080/api/v1/admin/members',headers={'X-SignalDesk-Key':s.api_key})
with urlopen(request,timeout=5) as response: assert response.status==200
print('API configuration and authentication verified')
"""
    run([PYTHON,'-c',probe])
    emit('verified', health='ok', services='active', migration='20261008_03', ocr_configured=True,
         real_paid_ocr_test=False, native_hermes_knowledge_base='preserved')

if __name__ == '__main__':
    os.umask(0o077)
    parser=argparse.ArgumentParser(); parser.add_argument('phase',choices=['prepare','deploy','verify']); parser.add_argument('--payload-sha')
    ARGS=parser.parse_args()
    assert os.geteuid()==0 and APP.is_dir() and STAGE.is_dir()
    os.chdir(APP); sys.path.insert(0,str(APP))
    try: globals()[ARGS.phase]()
    except Exception as error:
        import traceback
        (STAGE/'failure.private.log').write_text(traceback.format_exc())
        emit('failed',error_type=type(error).__name__,diagnostic='server-local private log'); sys.exit(1)
