"""Bounded, server-local deployment of the September 2026 membership upgrade.

Run using /opt/signaldesk-task/.venv/bin/python on the verified production host.
No credentials, database dumps, account data, or production source leave the host.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

APP = Path('/opt/signaldesk-task')
PLUGIN = Path('/home/hermes/.hermes/plugins/shared-task-router')
STAGE = Path('/var/tmp/signaldesk-members-20260928')
PYTHON = APP / '.venv/bin/python'
API = 'signaldesk-task-api.service'
WORKER = 'signaldesk-task-worker.service'
BACKEND_FILES = [
    'app/auth.py', 'app/auth_routes.py', 'app/main.py', 'app/models.py',
    'app/schemas.py', 'app/worker.py', 'app/member_service.py', 'app/members.py',
    'migrations/versions/20260928_01_member_management.py', 'requirements.txt',
]
BASE_HASHES = {
    'app/auth.py': '5d3fc7ed0bcec9e1d4ea895ac89107f8074f3bd3710c6a34a84644d346eeafc6',
    'app/auth_routes.py': '636635bb9867851f6ba767b9b998507985e6be192ab8047c2f457ddf0bed98c6',
    'app/main.py': '49d267fe9fa1ebedbcc0b043576a97e227c67ce893ffe6b5c4fc5056fe4d39bb',
    'app/models.py': '0c918b1e7387424d35c7e36c4c54d6c28f283e4c1c432117b910b21a25d4a8aa',
    'app/schemas.py': '74e0179b12a9e28e4e6c39b3901c7093190bfc0da0a7be0b16bddca3e79e3b86',
    'app/worker.py': 'a343391cc4809eb3af27d7a61e456c9a9d1dd38f65b692885b6a78ab7125ce58',
    'requirements.txt': 'd0cd61d82e70416db0dabd8a1b49a3110929fa5adcb1112cca50c647d43b1e4d',
}
PLUGIN_BASE = '454b3612e413706efca8d8acce78225c029fd952401ba1328d26fb7e211a4a95'
GUARD_SITE = '        staff_by_name = self._staff_by_name(staff)\n        if any(item.kind == "clarify" for item in intents):'
GUARD_NEW = ('        staff_by_name = self._staff_by_name(staff)\n'
             '        if not self_name or not recipient_id or self_name not in staff_by_name:\n'
             '            return "当前发送账号没有绑定负责人，或人员已停用、移除。请联系管理员在用户管理中启用人员并绑定消息渠道。"\n'
             '        if any(item.kind == "clarify" for item in intents):')
COUNTS = ['staff', 'user_account', 'user_session', 'task', 'task_event',
          'content_account', 'content_episode', 'notification_outbox']


def emit(phase, **values):
    print(json.dumps({'phase': phase, **values}, ensure_ascii=False), flush=True)


def sha(path, normalized=False):
    data = path.read_text().encode() if normalized else path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def run(args, timeout=60, cwd=APP, env=None):
    result = subprocess.run([str(x) for x in args], cwd=cwd, env=env,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Keep command output private on the same host, rather than exposing
        # connection details or a credential-bearing traceback to the client.
        with (STAGE / 'commands.private.log').open('a') as log:
            log.write(f'command={args[0]} rc={result.returncode}\n{result.stdout}\n{result.stderr}\n')
        raise RuntimeError(f'Command failed: {Path(args[0]).name}, code {result.returncode}; see server-local log')
    return result.stdout


def db_snapshot():
    from app.config import get_settings
    from sqlalchemy import create_engine, text
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        counts = {table: conn.execute(text('SELECT COUNT(*) FROM ' + table)).scalar() for table in COUNTS}
        revision = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
        admins = conn.execute(text("SELECT COUNT(*) FROM user_account u JOIN staff s ON u.staff_id=s.id WHERE u.role='admin' AND u.enabled=1 AND s.enabled=1")).scalar()
        has_column = bool(conn.execute(text("SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='staff' AND COLUMN_NAME='removed_at'")).scalar())
    engine.dispose()
    return {'revision': revision, 'counts': counts, 'enabled_admin_count': admins, 'removed_at_column': has_column}


def json_save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)


def assert_baseline():
    for relative, expected in BASE_HASHES.items():
        if sha(APP / relative, normalized=True) != expected:
            raise RuntimeError('Production code changed since preflight: ' + relative)
    for relative in set(BACKEND_FILES) - set(BASE_HASHES):
        if (APP / relative).exists():
            raise RuntimeError('Unexpected existing target: ' + relative)
    if sha(PLUGIN / 'router.py', normalized=True) != PLUGIN_BASE:
        raise RuntimeError('Production task router changed since preflight')
    if db_snapshot()['revision'] != '20260915_01':
        raise RuntimeError('Unexpected database migration baseline')


def prepare():
    assert_baseline()
    payload = STAGE / 'payload.tar.gz'
    if sha(payload) != ARGS.payload_sha:
        raise RuntimeError('Payload checksum mismatch')
    unpacked = STAGE / 'payload'
    unpacked.mkdir(exist_ok=False)
    allowed = {'server/' + p for p in BACKEND_FILES}
    with tarfile.open(payload, 'r:gz') as archive:
        members = archive.getmembers()
        if {m.name for m in members} != allowed or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive entries')
        for member in members:
            destination = unpacked / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target)
    staged_server = STAGE / 'candidate'
    staged_server.mkdir(exist_ok=False)
    shutil.copytree(APP / 'app', staged_server / 'app', ignore=shutil.ignore_patterns('__pycache__'))
    for relative in BACKEND_FILES:
        target = staged_server / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(unpacked / 'server' / relative, target)
    source = (PLUGIN / 'router.py').read_text()
    if source.count(GUARD_SITE) != 1:
        raise RuntimeError('Task router guard site is not unique')
    patched_router = STAGE / 'candidate-router.py'
    patched_router.write_text(source.replace(GUARD_SITE, GUARD_NEW))
    compile(patched_router.read_text(), str(patched_router), 'exec')
    for file in (staged_server / 'app').glob('*.py'):
        compile(file.read_text(), str(file), 'exec')
    probe = "import app.main, app.worker; routes={r.path for r in app.main.app.routes}; assert '/api/v1/admin/members' in routes; print('candidate imports ok')"
    env = {**os.environ, 'TASK_DATABASE_URL': 'sqlite://', 'TASK_API_KEY': 'deployment-probe-only',
           'TASK_HERMES_WEBHOOK_URL': '', 'PYTHONDONTWRITEBYTECODE': '1'}
    run([PYTHON, '-c', probe], cwd=staged_server, env=env)
    router_probe = """
import runpy
from types import SimpleNamespace
module = runpy.run_path('candidate-router.py')
router = object.__new__(module['TaskRouter'])
class NoWrites:
    def __getattr__(self, name):
        raise AssertionError('Unauthorized API access: ' + name)
router.api = NoWrites()
for kind in ['create','list','detail','delete','restore','complete']:
    prepared = SimpleNamespace(intents=[SimpleNamespace(kind=kind)],staff=[],platform='qq',self_name=None,recipient_id=None,creator='synthetic',message_id=None)
    result = router.execute(prepared)
    assert '没有绑定' in result
print('candidate task identity gate ok')
"""
    run([PYTHON, '-c', router_probe], cwd=STAGE, env=env)
    json_save(STAGE / 'prepared.json', {
        'payload_sha': sha(payload), 'backend_hashes': {p: sha(staged_server / p) for p in BACKEND_FILES},
        'router_sha': sha(patched_router), 'env_sha': sha(APP / '.env'),
        'config_sha': sha(APP / 'app/config.py'), 'plugin_init_sha': sha(PLUGIN / '__init__.py'),
    })
    emit('prepared', backend_files=len(BACKEND_FILES), plugin='minimal identity guard', checks='imports, syntax, blocked sender behavior')


def copy_atomic(source, target, ownership=None):
    info = target.stat() if target.exists() else ownership
    temporary = target.with_name('.' + target.name + '.members-upgrade-tmp')
    with temporary.open('xb') as out:
        out.write(source.read_bytes())
        out.flush()
        os.fsync(out.fileno())
    os.chmod(temporary, info.st_mode & 0o777 if target.exists() else 0o644)
    os.chown(temporary, info.st_uid, info.st_gid)
    os.replace(temporary, target)


def wait_health():
    import urllib.request
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:19080/health', timeout=2) as response:
                if response.status == 200 and json.loads(response.read()) == {'status': 'ok'}:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError('API did not become healthy')


def verify(expected_counts=None):
    import urllib.request
    from app.config import get_settings
    snapshot = db_snapshot()
    if snapshot['revision'] != '20260928_01' or not snapshot['removed_at_column']:
        raise RuntimeError('Migration verification failed')
    if expected_counts and any(snapshot['counts'][k] != expected_counts[k] for k in COUNTS):
        raise RuntimeError('Record counts changed during stopped-service migration')
    wait_health()
    request = urllib.request.Request('http://127.0.0.1:19080/api/v1/admin/members',
                                     headers={'X-SignalDesk-Key': get_settings().api_key})
    with urllib.request.urlopen(request, timeout=10) as response:
        users = json.loads(response.read())
        if response.status != 200 or len(users) != snapshot['counts']['staff']:
            raise RuntimeError('Membership endpoint verification failed')
        assert all('removed_at' in u and 'user' in u for u in users)
    try:
        urllib.request.urlopen('http://127.0.0.1:19080/api/v1/admin/members', timeout=10)
        raise RuntimeError('Unauthenticated management unexpectedly allowed')
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
    for unit in [API, WORKER]:
        if run(['systemctl', 'is-active', unit]).strip() != 'active':
            raise RuntimeError('Inactive service: ' + unit)
    prepared = json.loads((STAGE / 'prepared.json').read_text())
    assert sha(APP / '.env') == prepared['env_sha']
    assert sha(APP / 'app/config.py') == prepared['config_sha']
    assert sha(PLUGIN / '__init__.py') == prepared['plugin_init_sha']
    assert sha(PLUGIN / 'router.py') == prepared['router_sha']
    for relative, checksum in prepared['backend_hashes'].items():
        assert sha(APP / relative) == checksum
    state = json.loads(run(['docker', 'inspect', '--format', '{{json .State}}', 'hermes']))
    if not state['Running']:
        raise RuntimeError('Hermes container is not running')
    return {**snapshot, 'api_health': 'ok', 'admin_endpoint': 200, 'unauthenticated': 401,
            'services': 'active', 'hermes_running': True, 'config_preserved': True}


def deploy():
    assert_baseline()
    prepared = json.loads((STAGE / 'prepared.json').read_text())
    assert sha(APP / '.env') == prepared['env_sha']
    assert sha(APP / 'app/config.py') == prepared['config_sha']
    assert sha(PLUGIN / '__init__.py') == prepared['plugin_init_sha']
    for unit in [API, WORKER]:
        assert run(['systemctl', 'is-active', unit]).strip() == 'active'
    for relative, checksum in prepared['backend_hashes'].items():
        assert sha(STAGE / 'candidate' / relative) == checksum
    assert sha(STAGE / 'candidate-router.py') == prepared['router_sha']
    backup = Path('/var/backups/signaldesk-task') / time.strftime('members-%Y%m%dT%H%M%SZ', time.gmtime())
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tarfile.open(backup / 'code-and-config.tar.gz', 'w:gz') as archive:
        for name in ['app', 'migrations', 'alembic.ini', 'requirements.txt', '.env']:
            archive.add(APP / name, arcname='server/' + name,
                        filter=lambda m: None if '__pycache__' in m.name else m)
        archive.add(PLUGIN, arcname='shared-task-router',
                    filter=lambda m: None if '__pycache__' in m.name else m)
    (backup / 'requirements-before.txt').write_text(run([PYTHON, '-m', 'pip', 'freeze']))
    originals = backup / 'originals'
    for relative in BACKEND_FILES:
        if (APP / relative).exists():
            target = originals / 'server' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(APP / relative, target)
    (originals / 'plugin').mkdir(parents=True)
    shutil.copy2(PLUGIN / 'router.py', originals / 'plugin/router.py')
    json_save(STAGE / 'deployment.json', {'backup': str(backup), 'phase': 'code-backup-ready'})
    emit('code_backup_ready', backup=str(backup))
    installed = False
    try:
        run(['systemctl', 'stop', WORKER, API], timeout=60)
        before = db_snapshot()
        json_save(backup / 'deployment-state.json', before)
        from app.config import get_settings
        from sqlalchemy.engine import make_url
        url = make_url(get_settings().database_url)
        if url.get_backend_name() != 'mysql':
            raise RuntimeError('Unexpected database backend')
        env = {**os.environ, 'MYSQL_PWD': url.password or ''}
        args = ['mysqldump', '--single-transaction', '--quick', '--hex-blob', '--no-tablespaces',
                '--set-gtid-purged=OFF', '--triggers', '--routines', '--events',
                '--host=' + (url.host or 'localhost'), '--port=' + str(url.port or 3306),
                '--user=' + (url.username or ''), '--databases', url.database]
        with (backup / 'database.sql').open('xb') as out, (backup / 'dump.private.log').open('xb') as errors:
            result = subprocess.run(args, stdout=out, stderr=errors, env=env, timeout=120)
        if result.returncode or (backup / 'database.sql').stat().st_size < 100:
            raise RuntimeError('Database backup failed; see server-local dump log')
        emit('database_backup_ready', bytes=(backup / 'database.sql').stat().st_size)
        # Set before the first replacement so any partial copy is recoverable.
        installed = True
        for relative in BACKEND_FILES:
            copy_atomic(STAGE / 'candidate' / relative, APP / relative, (APP / 'app/main.py').stat())
        copy_atomic(STAGE / 'candidate-router.py', PLUGIN / 'router.py')
        run([PYTHON, '-m', 'alembic', 'upgrade', 'head'], timeout=60)
        after = db_snapshot()
        assert after['revision'] == '20260928_01' and after['removed_at_column']
        assert after['counts'] == before['counts']
        emit('migrated', revision=after['revision'], historical_record_counts_unchanged=True)
        run(['systemctl', 'start', API])
        wait_health()
        run(['docker', 'restart', '--time', '20', 'hermes'], timeout=60)
        run(['systemctl', 'start', WORKER])
        result = verify()
        json_save(backup / 'deployment-result.json', result)
        json_save(STAGE / 'deployment.json', {'backup': str(backup), 'phase': 'complete'})
        emit('deployed', backup=str(backup), **result)
    except Exception:
        if installed:
            run(['systemctl', 'stop', WORKER, API], timeout=60)
            for relative in BACKEND_FILES:
                previous = originals / 'server' / relative
                target = APP / relative
                if previous.exists():
                    copy_atomic(previous, target, previous.stat())
                elif target.exists():
                    # Keep new files recoverable; never remove business data.
                    retained = backup / 'rolled-back-new-files' / relative
                    retained.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(retained))
            copy_atomic(originals / 'plugin/router.py', PLUGIN / 'router.py')
            run(['docker', 'restart', '--time', '20', 'hermes'], timeout=60)
        run(['systemctl', 'start', API, WORKER], timeout=60)
        wait_health()
        json_save(STAGE / 'deployment.json', {'backup': str(backup), 'phase': 'rolled-back-code'})
        emit('rolled_back_code', backup=str(backup), schema='additive column is retained if migration ran')
        raise


if __name__ == '__main__':
    os.umask(0o077)
    if os.geteuid() != 0 or not APP.is_dir() or not STAGE.is_dir():
        raise SystemExit('Run on the verified host as the authorized deployment user')
    os.chdir(APP)
    sys.path.insert(0, str(APP))
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare', 'deploy', 'verify'])
    parser.add_argument('--payload-sha')
    ARGS = parser.parse_args()
    try:
        if ARGS.phase == 'prepare':
            prepare()
        elif ARGS.phase == 'deploy':
            deploy()
        else:
            emit('verified', **verify())
    except Exception as error:
        import traceback
        (STAGE / 'failure.private.log').write_text(traceback.format_exc())
        emit('failed', error_type=type(error).__name__, diagnostic='server-local failure.private.log')
        raise SystemExit(1)
