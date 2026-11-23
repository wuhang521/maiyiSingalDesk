"""Run inside Hermes as its owning uid; preserve native copying and authorizations."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

from gateway.control_socket import reload_gateway_plugins
from hermes_cli.config import atomic_config_replace, read_user_config_raw
from utils import atomic_write_text

ROOT = Path('/opt/data')
NAMES = ['default', 'chenying', 'futaotao', 'jiege', 'songwei', 'weixin-minjie', 'weixin-suzong', 'weixin-wuhang']
BEFORE = '469624f01a9a57b4369f2a4a5d21ee58d5b3a0182c4fabf098a81d6e9e900b96'
PREFIX_BEFORE = 'c27ea0af5985235f5b4aba19e3ab7f0a4a29efc52816966b371588a6891f252c'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n', mode=0o600)


def home(name):
    return ROOT if name == 'default' else ROOT / 'profiles' / name


def protected(directory):
    paths = [directory / name for name in ['.env', 'SOUL.md', 'AGENTS.md', 'auth.json', 'personal-commute-weather.json']]
    cron = directory / 'cron'
    if cron.exists():
        paths += [p for p in cron.rglob('*') if p.is_file() and p.suffix == '.json']
    return {str(p): sha(p) for p in paths if p.is_file()}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage'); parser.add_argument('--deploy', action='store_true')
    args = parser.parse_args(); stage = Path(args.stage).resolve()
    assert stage.parent == Path('/tmp') and stage.name.startswith('hermes-onboarding-release-')
    manifest = json.loads((stage / 'manifest.json').read_text())
    for name, digest in manifest.items():
        if name.startswith('plugins/'):
            assert sha(stage / name) == digest, name
    sys.path.insert(0, str(stage / 'plugins/shared-task-router'))
    import onboarding
    import onboarding_gateway
    configs = {}; owners = {}; originals = {}; preserved = {}
    targets = {}
    for name in NAMES:
        directory = home(name); onboarding._plain(directory)
        configs[name] = read_user_config_raw(directory / 'config.yaml')
        assert 'shared-task-router' in configs[name]['plugins']['enabled'], name
        owners[name] = onboarding_gateway.wechat_owner(directory)
        assert bool(owners[name]) == (name not in {'default', 'jiege'}), name
        preserved.update(protected(directory))
        folder = directory / 'plugins/shared-task-router'
        assert sha(folder / '__init__.py') == BEFORE, name
        for source in (stage / 'plugins/shared-task-router').iterdir():
            if source.is_file():
                targets[folder / source.name] = source
        prefix = directory / 'plugins/channel-nickname-prefix'
        if prefix.exists():
            assert name in {'default', 'futaotao', 'songwei'} and sha(prefix / '__init__.py') == PREFIX_BEFORE
            targets[prefix / '__init__.py'] = stage / 'plugins/channel-nickname-prefix/__init__.py'
        state = directory / onboarding.STATE
        assert not state.exists(), 'Existing onboarding state needs a separate reviewed upgrade: ' + name
    for path in targets:
        onboarding._plain(path)
        originals[path] = path.read_bytes() if path.exists() else None
    if not args.deploy:
        print(json.dumps({'passed': True, 'mode': 'prepared', 'profiles': NAMES, 'existing_owners': sum(bool(v) for v in owners.values()),
                          'runtime_files': len(targets), 'wechat_messages_sent': 0})); return
    backup = ROOT / '.onboarding-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True, mode=0o700)
    for path, content in originals.items():
        if content is not None:
            target = backup / 'files' / path.relative_to(ROOT); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    for name in NAMES:
        target = backup / name; target.mkdir()
        shutil.copy2(home(name) / 'config.yaml', target / 'config.yaml')
    reloads = {}
    try:
        for path, source in targets.items():
            atomic_write_text(path, source.read_text('utf-8'), mode=0o600)
        for name in NAMES:
            directory = home(name)
            if owners[name]:
                onboarding.save_state(directory, {'schema': 1, 'owner': onboarding.owner_key(name, owners[name]),
                    'step': 'done', 'seen': {}, 'migrated_existing_member': True})
            updated = copy.deepcopy(configs[name]); updated['personal_onboarding'] = {'enabled': True}
            atomic_config_replace(directory / 'config.yaml', updated)
            assert read_user_config_raw(directory / 'config.yaml') == updated
        for name in NAMES:
            result = reload_gateway_plugins(ROOT, profile_home=home(name), timeout=30)
            assert result and result.get('reloaded'), (name, result)
            reloads[name] = result
        assert all(sha(Path(path)) == digest for path, digest in preserved.items()), 'Protected config changed during deployment'
        for name in NAMES:
            directory = home(name)
            state = onboarding.read_state(directory)
            if owners[name]:
                assert state['step'] == 'done' and state['owner'] == onboarding.owner_key(name, owners[name])
            else:
                assert state is None
        assert all(sha(path) == sha(source) for path, source in targets.items())
        report = {'passed': True, 'mode': 'deployed', 'backup': str(backup), 'profiles': NAMES,
            'migrated_existing_members': 6, 'default_no_connection': True, 'jiege_qq_only_unchanged': True,
            'protected_config_preserved': True, 'native_copy_unchanged': True, 'native_authorizations_unchanged': True,
            'wechat_messages_sent': 0, 'reloads': reloads,
            'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in targets}}
        save(backup / 'receipt.json', report); save(stage / 'profiles-deployment.json', report)
        print(json.dumps({key: value for key, value in report.items() if key not in {'reloads', 'source_sha256'}}))
    except Exception:
        for path, content in originals.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_text(path, content.decode('utf-8'), mode=0o600)
        for name in NAMES:
            atomic_config_replace(home(name) / 'config.yaml', configs[name])
            (home(name) / onboarding.STATE).unlink(missing_ok=True)
        for name in NAMES:
            reload_gateway_plugins(ROOT, profile_home=home(name), timeout=30)
        raise


if __name__ == '__main__':
    main()
