"""Upgrade the three existing prefix copies after the native module-path regression test."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

from gateway.control_socket import reload_gateway_plugins
from utils import atomic_write_text

stage = Path(sys.argv[1]).resolve()
assert stage.parent == Path('/tmp') and stage.name.startswith('hermes-onboarding-release-')
source = stage / 'plugins/channel-nickname-prefix/__init__.py'
spec = importlib.util.spec_from_file_location('prefix_release_check', source)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.register(None)  # Real native adapters in this isolated CLI process; no connection or send.
root = Path('/opt/data')
homes = [root, root / 'profiles/futaotao', root / 'profiles/songwei']
targets = [p / 'plugins/channel-nickname-prefix/__init__.py' for p in homes]
before = {p: p.read_bytes() for p in targets}
assert all(hashlib.sha256(data).hexdigest() == '54b830716d085f6582f983306aefd8afc8f94490c4ee1cfb58fd8e02783e7a2f'
           for data in before.values())
protected = {p / n: (p / n).read_bytes() for p in homes for n in ['config.yaml', '.env', 'SOUL.md']}
backup = Path(tempfile.mkdtemp(prefix='prefix-native-path-', dir=root / '.onboarding-backups'))
for index, path in enumerate(targets):
    atomic_write_text(backup / (str(index) + '.py'), before[path].decode(), mode=0o600)
try:
    for p in targets:
        atomic_write_text(p, source.read_text(), mode=0o600)
    for home in homes:
        result = reload_gateway_plugins(root, profile_home=home, timeout=30)
        assert result and result['reloaded'] and 'channel-nickname-prefix' in result['plugins']
        assert any('member-onboarding-result' in a.get('activated_now', {}).get('gateway_commands', []) for a in result['activations'])
    assert all(p.read_bytes() == data for p, data in protected.items())
    result = {'passed': True, 'native_registration': True, 'profiles': ['default', 'futaotao', 'songwei'],
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'backup': str(backup),
              'protected_config_unchanged': True, 'wechat_messages_sent': 0}
    atomic_write_text(stage / 'prefix-deployment.json', json.dumps(result, indent=2) + '\n', mode=0o600)
    print(json.dumps(result))
except Exception:
    for p, data in before.items():
        atomic_write_text(p, data.decode(), mode=0o600)
    for home in homes:
        reload_gateway_plugins(root, profile_home=home, timeout=30)
    raise
