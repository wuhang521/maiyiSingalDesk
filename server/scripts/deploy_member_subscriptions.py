"""Release one reviewed API file, preserving live configuration and all subscription data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, '/opt/signaldesk-task')
from deploy_channel_subscriptions import APP, BACKUPS, PYTHON, atomic, database, dump_database, run

SERVICES = ('signaldesk-task-api.service', 'signaldesk-task-worker.service', 'signaldesk-channel-push.service')
TARGET = APP / 'app/channel_subscriptions.py'
BEFORE = '4d3da2297f82215d10c0dadf0cc2d23969e12d520fd9ba325c8cfe526b82a91e'
TABLES = ('staff', 'staff_channel', 'user_account', 'smart_channel', 'channel_subscription', 'channel_push_config')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

def http(path, expected=200, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request('http://127.0.0.1:19080' + path, data=data,
                      headers={'Content-Type': 'application/json', **(headers or {})},
                      method='PUT' if data is not None else 'GET')
    try:
        response = urlopen(request, timeout=15)
    except HTTPError as error:
        response = error
    assert response.code == expected, (path, response.code, expected)
    return json.loads(response.read())

def fingerprint(engine):
    result = {}
    with engine.connect() as connection:
        for table in TABLES:
            rows = connection.exec_driver_sql('SELECT * FROM `' + table + '`').all()
            values = sorted(json.dumps(list(row), default=str) for row in rows)
            result[table] = {'rows': len(rows), 'sha256': hashlib.sha256(json.dumps(values).encode()).hexdigest()}
    return result

def live_auth_smoke(engine):
    from datetime import timedelta
    from sqlalchemy import delete, select
    from sqlalchemy.orm import Session
    from app.auth import create_user_session
    from app.models import Staff, UserAccount, UserSession, utcnow
    sessions = []
    try:
        headers = []
        with Session(engine) as db:
            for role in ('admin', 'member'):
                user = db.scalar(select(UserAccount).join(Staff).where(UserAccount.role == role,
                    UserAccount.enabled.is_(True), Staff.enabled.is_(True), Staff.removed_at.is_(None)))
                assert user is not None
                token, session = create_user_session(db, user)
                session.expires_at = utcnow() + timedelta(minutes=3)
                db.flush(); sessions.append(session.token_hash)
                headers.append({'Authorization': 'Bearer ' + token})
                staff_id = user.staff_id
            db.commit()
        route = '/api/v1/channel-subscriptions/members/' + str(staff_id)
        state = http(route, headers=headers[0])
        http(route, 403, headers[1])
        # Deliberately stale fingerprint must fail before any subscription write.
        http(route, 409, headers[0], {'previous_channel_ids': state['subscribed_channel_ids'],
            'channel_ids': state['subscribed_channel_ids'], 'binding_revision': '0' * 64})
        return {'administrator_read': 200, 'member_denied': 403, 'stale_binding_rejected': 409,
                'temporary_auth_sessions_removed': True}
    finally:
        with Session(engine) as db:
            db.execute(delete(UserSession).where(UserSession.token_hash.in_(sessions))); db.commit()

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('mode', choices=['prepare', 'deploy'])
    parser.add_argument('--sha256', required=True); args = parser.parse_args()
    stage = Path(__file__).resolve().parent
    payload = stage / 'channel_subscriptions.py'
    assert payload.resolve() == payload and sha(payload) == args.sha256
    assert TARGET.resolve() == TARGET and sha(TARGET) == BEFORE
    for service in SERVICES:
        assert run(['systemctl', 'is-active', service]).strip() == 'active'
    settings, url, engine = database()
    http('/health')
    with tempfile.TemporaryDirectory(dir=stage) as directory:
        isolated = Path(directory)
        shutil.copytree(APP / 'app', isolated / 'app', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(payload, isolated / 'app/channel_subscriptions.py')
        env = {'PATH': os.defpath, 'PYTHONPATH': str(isolated), 'PYTHONDONTWRITEBYTECODE': '1',
            'TASK_DATABASE_URL': 'sqlite:///' + str(isolated / 'smoke.db'),
            'TASK_API_KEY': 'isolated-release-service', 'TASK_CHANNEL_PUSH_ENABLED': 'false'}
        smoke = json.loads(run([PYTHON, stage / 'check_member_subscriptions_release.py'], cwd=isolated, env=env).splitlines()[-1])
    save(stage / 'prepared.json', {'passed': True, 'before': BEFORE, 'after': args.sha256, 'smoke': smoke})
    if args.mode == 'prepare':
        print(json.dumps({'passed': True, 'mode': 'prepared', 'isolated_checks': smoke})); return
    BACKUPS.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='member-subscriptions-069-', dir=BACKUPS))
    shutil.copy2(TARGET, backup / TARGET.name)
    config_hashes = {str(p): sha(p) for p in (APP / '.env', Path('/etc/signaldesk-channel-push/wechat-routes.json'))}
    info = TARGET.stat(); before = None; replaced = False
    try:
        run(['systemctl', 'stop', *SERVICES])
        dump_database(backup, url)
        before = fingerprint(engine); save(backup / 'database-before.json', before)
        assert sha(TARGET) == BEFORE
        atomic(TARGET, payload.read_bytes(), info); replaced = True
        run(['systemctl', 'start', *SERVICES])
        for _ in range(30):
            try:
                http('/health'); break
            except Exception:
                time.sleep(1)
        paths = http('/openapi.json')['paths']
        assert {'get', 'put'} <= set(paths['/api/v1/channel-subscriptions/members/{staff_id}'])
        http('/api/v1/channel-subscriptions/members/1', 401)
        http('/api/v1/channel-subscriptions/members/1', 401, {'X-SignalDesk-Key': settings.api_key})
        auth = live_auth_smoke(engine)
        after = fingerprint(engine); assert after == before
        assert all(sha(Path(p)) == h for p, h in config_hashes.items())
        assert sha(TARGET) == args.sha256
        for service in SERVICES:
            assert run(['systemctl', 'is-active', service]).strip() == 'active'
        result = {'passed': True, 'mode': 'deployed', 'version': '0.6.9', 'backup': str(backup),
                  'source_sha256': args.sha256, 'data_preserved': after, 'auth': auth,
                  'configuration_preserved': True, 'migration': 'none', 'subscription_writes': 0}
        save(stage / 'production-deployment.json', result); save(backup / 'receipt.json', result)
        print(json.dumps(result))
    except Exception:
        if replaced:
            run(['systemctl', 'stop', *SERVICES])
            atomic(TARGET, (backup / TARGET.name).read_bytes(), info)
        run(['systemctl', 'start', *SERVICES]); raise
    finally:
        engine.dispose()

if __name__ == '__main__':
    main()
