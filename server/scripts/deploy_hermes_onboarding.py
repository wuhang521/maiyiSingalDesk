"""Narrow onboarding API rollout; no member subscription writes or schema changes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, '/opt/signaldesk-task')
from deploy_channel_subscriptions import APP, BACKUPS, atomic, database, dump_database, run

BASELINE = {'channel_subscriptions.py': 'bd785613da31b31dfdf3ffae78dfc6ee31228c8fc4ab29348c8157b7bbafc9af',
            'main.py': 'e81686ccd46d2eeb660361cc4ea1deb6cf431967f2bd6de5f13f5ac9f7e7b7d7'}
SERVICES = ('signaldesk-task-api.service', 'signaldesk-task-worker.service', 'signaldesk-channel-push.service')
TABLES = ('staff', 'staff_channel', 'user_account', 'smart_channel', 'channel_subscription', 'channel_push_config')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def request(path, expected=200, *, key=None, body=None, method='GET'):
    req = Request('http://127.0.0.1:19080' + path,
                  data=None if body is None else json.dumps(body).encode(), method=method,
                  headers={'Content-Type': 'application/json', **({'X-SignalDesk-Key': key} if key else {})})
    try:
        response = urlopen(req, timeout=12)
    except HTTPError as error:
        response = error
    assert response.code == expected, (path, response.code, expected)
    return json.loads(response.read())


def fingerprint(engine):
    result = {}
    with engine.connect() as connection:
        for table in TABLES:
            values = sorted(json.dumps(list(row), default=str) for row in connection.exec_driver_sql('SELECT * FROM `' + table + '`'))
            result[table] = {'rows': len(values), 'sha256': hashlib.sha256(json.dumps(values).encode()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage'); parser.add_argument('--deploy', action='store_true')
    args = parser.parse_args(); stage = Path(args.stage).resolve()
    assert stage == Path('/home/hermes/desktop-fixes/member-onboarding-20261123')
    manifest = json.loads((stage / 'manifest.json').read_text())
    native = json.loads((stage / 'native-verification.json').read_text())
    assert native['passed'] and all(row['native_default_clone'] and row['native_persona_loader'] for row in native['profiles'])
    for name in ('channel_onboarding.py', 'channel_subscriptions.py'):
        assert sha(stage / 'backend' / name) == manifest['backend/' + name]
    for name, digest in BASELINE.items():
        assert sha(APP / 'app' / name) == digest, name
    assert not (APP / 'app/channel_onboarding.py').exists()
    text = (APP / 'app/main.py').read_text()
    for anchor, addition in [('from .channel_subscriptions import router as channel_subscriptions_router',
                              'from .channel_onboarding import router as channel_onboarding_router'),
                             ('app.include_router(channel_subscriptions_router)', 'app.include_router(channel_onboarding_router)')]:
        assert text.count(anchor) == 1 and addition not in text
        text = text.replace(anchor, anchor + '\n' + addition)
    payloads = {'main.py': text.encode(), **{name: (stage / 'backend' / name).read_bytes()
                for name in ('channel_onboarding.py', 'channel_subscriptions.py')}}
    for name, data in payloads.items():
        compile(data, name, 'exec')
    if not args.deploy:
        print(json.dumps({'passed': True, 'mode': 'prepared', 'native_checks': True, 'files': list(payloads)})); return
    for service in SERVICES:
        assert run(['systemctl', 'is-active', service]).strip() == 'active'
    settings, url, engine = database(); request('/health')
    BACKUPS.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='hermes-onboarding-', dir=BACKUPS))
    metadata = {}
    for name in BASELINE:
        target = APP / 'app' / name
        shutil.copy2(target, backup / name); metadata[name] = target.stat()
    unchanged = {str(p): sha(p) for p in [APP / '.env', Path('/etc/signaldesk-channel-push/wechat-routes.json')]}
    replaced = False
    try:
        run(['systemctl', 'stop', *SERVICES])
        dump_database(backup, url)
        before = fingerprint(engine); save(backup / 'database-before.json', before)
        assert all(sha(APP / 'app' / name) == digest for name, digest in BASELINE.items())
        replaced = True
        for name, data in payloads.items():
            atomic(APP / 'app' / name, data, metadata.get(name, metadata['main.py']))
        run(['systemctl', 'start', *SERVICES])
        for _ in range(30):
            try:
                request('/health'); break
            except OSError:
                time.sleep(1)
        paths = request('/openapi.json')['paths']
        assert 'post' in paths['/api/v1/channel-onboarding/catalog']
        assert 'put' in paths['/api/v1/channel-onboarding/subscriptions']
        unknown = {'profile': 'nonexistent-onboarding-probe', 'recipient_id': 'nonexistent-onboarding-probe@im.wechat'}
        request('/api/v1/channel-onboarding/catalog', 401, body=unknown, method='POST')
        request('/api/v1/channel-onboarding/catalog', 404, key=settings.api_key, body=unknown, method='POST')
        after = fingerprint(engine); assert before == after
        assert all(sha(Path(p)) == digest for p, digest in unchanged.items())
        assert all(run(['systemctl', 'is-active', service]).strip() == 'active' for service in SERVICES)
        result = {'passed': True, 'mode': 'deployed', 'backup': str(backup), 'migration': 'none',
                  'files': {name: sha(APP / 'app' / name) for name in payloads},
                  'data_preserved': after, 'configuration_preserved': True, 'subscription_writes': 0,
                  'smoke': {'health': 200, 'anonymous_denied': 401, 'unknown_receiver_denied': 404}}
        save(stage / 'backend-deployment.json', result); save(backup / 'receipt.json', result)
        print(json.dumps(result))
    except Exception:
        if replaced:
            run(['systemctl', 'stop', *SERVICES])
            for name in BASELINE:
                atomic(APP / 'app' / name, (backup / name).read_bytes(), metadata[name])
            (APP / 'app/channel_onboarding.py').unlink(missing_ok=True)
        run(['systemctl', 'start', *SERVICES]); raise
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
