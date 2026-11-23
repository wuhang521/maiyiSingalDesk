"""Exercise staged plugins in the real Hermes runtime against a disposable HTTP backend."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen
import uuid


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=70, **kwargs)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage'); args = parser.parse_args()
    stage = Path(args.stage).resolve()
    assert stage.parent == Path('/home/hermes/desktop-fixes') and stage.name == 'member-onboarding-20261123'
    evidence = stage / ('native-probe-' + uuid.uuid4().hex[:8]); evidence.mkdir()
    backend = evidence / 'backend'; backend.mkdir()
    shutil.copytree('/opt/signaldesk-task/app', backend / 'app', ignore=shutil.ignore_patterns('__pycache__'))
    for name in ['channel_onboarding.py', 'channel_subscriptions.py']:
        shutil.copyfile(stage / 'backend' / name, backend / 'app' / name)
    main_path = backend / 'app/main.py'
    text = main_path.read_text()
    for anchor, addition in [('from .channel_subscriptions import router as channel_subscriptions_router',
                              'from .channel_onboarding import router as channel_onboarding_router'),
                             ('app.include_router(channel_subscriptions_router)', 'app.include_router(channel_onboarding_router)')]:
        if addition not in text:
            assert text.count(anchor) == 1; text = text.replace(anchor, anchor + '\n' + addition)
    main_path.write_text(text)
    routes = evidence / 'routes.json'
    routes.write_text(json.dumps({'bindings': {}, 'routes': {name: {
        'url': f'http://127.0.0.1:9876/p/{name}/webhooks/signaldesk-channel-weixin-{name}',
        'secret': 'synthetic-route-key'} for name in ['fixture-a', 'fixture-b']}}))
    host = '172.18.0.1'
    with socket.socket() as listener:
        listener.bind((host, 0)); port = listener.getsockname()[1]
    env = {**os.environ, 'PYTHONPATH': str(backend), 'TASK_DATABASE_URL': 'sqlite:///' + str(evidence / 'onboarding-e2e.db'),
        'TASK_API_KEY': 'synthetic-onboarding-service', 'TASK_HERMES_WEBHOOK_URL': '',
        'TASK_CHANNEL_PUSH_ENABLED': 'false', 'TASK_CHANNEL_WECHAT_ROUTES_FILE': str(routes),
        'TASK_PROMPT_API_KEY': '', 'PYTHONDONTWRITEBYTECODE': '1'}
    namespace = '/tmp/member-onboarding-probe-' + uuid.uuid4().hex
    server_log = (evidence / 'server.log').open('w')
    server = subprocess.Popen(['/opt/signaldesk-task/.venv/bin/python', str(stage / 'onboarding_preview.py'), host, str(port)],
                              cwd=backend, env=env, stdout=server_log, stderr=subprocess.STDOUT)
    python = '/opt/hermes/.venv/bin/python'
    try:
        for _ in range(40):
            if server.poll() is not None:
                raise RuntimeError('Preview backend stopped: ' + (evidence / 'server.log').read_text()[-1500:])
            try:
                with urlopen(f'http://{host}:{port}/health', timeout=1) as response:
                    if response.code == 200:
                        break
            except OSError:
                time.sleep(.25)
        run(['docker', 'exec', '-u', '1001:1001', 'hermes', 'mkdir', '-p', namespace])
        run(['docker', 'cp', str(stage / 'plugins'), 'hermes:' + namespace + '/plugins'])
        run(['docker', 'cp', str(stage / 'hermes-onboarding-native.py'), 'hermes:' + namespace + '/native.py'])
        for folder, pattern, log in [('shared-task-router', 'test_onboarding*.py', 'onboarding-unit.log'),
                                     ('shared-task-router', 'test_self_create.py', 'task-regression.log'),
                                     ('channel-nickname-prefix', 'test_personal_prefix.py', 'prefix-unit.log')]:
            result = run(['docker', 'exec', '-u', '1001:1001', 'hermes', python, '-m', 'unittest', 'discover',
                          '-s', namespace + '/plugins/' + folder + '/tests', '-p', pattern])
            (evidence / log).write_text(result.stdout + result.stderr)
        rows = []
        for name in ['fixture-a', 'fixture-b']:
            result = run(['docker', 'exec', '-u', '1001:1001', '-e', 'HERMES_HOME=' + namespace + '/profiles/' + name,
                'hermes', python, namespace + '/native.py', namespace + '/profiles/' + name,
                f'http://{host}:{port}', namespace + '/plugins', namespace + '/evidence'])
            (evidence / (name + '.log')).write_text(result.stdout + result.stderr)
            rows.append(json.loads(result.stdout.splitlines()[-1]))
        run(['docker', 'cp', 'hermes:' + namespace + '/evidence', str(evidence / 'conversations')])
        run(['docker', 'cp', 'hermes:' + namespace + '/profiles', str(evidence / 'profiles')])
        result = {'passed': True, 'evidence': str(evidence), 'profiles': rows,
                  'runtime': 'actual Hermes container, native PluginContext/PluginManager',
                  'database': 'disposable SQLite; actual HTTP', 'production_subscription_writes': 0, 'wechat_messages_sent': 0}
        (stage / 'native-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
    except subprocess.CalledProcessError as error:
        (evidence / 'process-error.log').write_text((error.stdout or '') + (error.stderr or ''))
        raise RuntimeError('Native runtime test failed; see ' + str(evidence / 'process-error.log')) from None
    finally:
        server.terminate()
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill(); server.wait(timeout=5)
        server_log.close()
        assert namespace.startswith('/tmp/member-onboarding-probe-') and Path(namespace).parent == Path('/tmp')
        cleanup = 'import pathlib,shutil,sys;p=pathlib.Path(sys.argv[1]);assert p.parent==pathlib.Path("/tmp") and p.name.startswith("member-onboarding-probe-");shutil.rmtree(p)'
        run(['docker', 'exec', '-u', '0:0', 'hermes', python, '-c', cleanup, namespace])


if __name__ == '__main__':
    main()
