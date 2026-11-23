"""Run the shipped plugin through native registrations and real isolated HTTP APIs."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.request import Request, urlopen
from unittest.mock import patch

home, base, source, evidence = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
assert not home.exists() and base.startswith(('http://127.0.0.1:', 'http://172.18.0.1:'))
profile = home.name
root = home.parent.parent
os.environ['HERMES_HOME'] = str(root)
template_soul = '保留原有助手规则与个人授权边界。\n'
(root / 'SOUL.md').write_text(template_soul, encoding='utf-8')
(root / '.env').write_text('TASK_API_BASE_URL=' + base
    + '\nTASK_API_KEY=synthetic-onboarding-service\n', encoding='utf-8')
(root / 'config.yaml').write_text(json.dumps({'personal_onboarding': {'enabled': True},
    'plugins': {'enabled': ['shared-task-router']}, 'terminal': {'persistent_approval': ['fixture-existing']}}), encoding='utf-8')
from hermes_constants import get_default_hermes_root
from hermes_cli import profiles
assert Path(get_default_hermes_root()) == root
with patch.object(profiles, '_maybe_register_gateway_service'), patch.object(profiles, '_notify_multiplexer'):
    cloned = profiles.create_profile(profile, clone_from='default', clone_config=True, no_alias=True)
assert cloned == home
(home / '.env').write_text('WEIXIN_ACCOUNT_ID=synthetic-account\nTASK_API_BASE_URL=' + base
    + '\nTASK_API_KEY=synthetic-onboarding-service\n', encoding='utf-8')
account = home / 'weixin/accounts'; account.mkdir(parents=True)
peer = profile + '@im.wechat'
(account / 'synthetic-account.json').write_text(json.dumps({'user_id': peer}), encoding='utf-8')
os.environ['HERMES_HOME'] = str(home)
config_before = (home / 'config.yaml').read_bytes()

from hermes_constants import get_hermes_home
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
assert Path(get_hermes_home()) == home
directory = home / 'plugins/shared-task-router'
spec = importlib.util.spec_from_file_location('native_onboarding_probe', directory / '__init__.py',
                                             submodule_search_locations=[str(directory)])
plugin = importlib.util.module_from_spec(spec); sys.modules[spec.name] = plugin; spec.loader.exec_module(plugin)
manager = PluginManager(scope_key=str(home))
manifest = PluginManifest(name='shared-task-router', key='shared-task-router', version='1.1.0',
                          path=str(directory), provides_hooks=['pre_gateway_dispatch'])
plugin.register(PluginContext(manifest, manager))
assert manager.has_hook('pre_gateway_dispatch')
assert 'member-onboarding-result' in manager._plugin_commands
source_event = SimpleNamespace(platform=SimpleNamespace(value='weixin'), user_id=peer, user_name='Fixture',
                                chat_id=peer, chat_type='dm', is_bot=False)
transcript = []

async def say(text, identifier):
    event = SimpleNamespace(text=text, source=source_event, message_id=identifier, internal=False)
    results = await manager.ainvoke_hook('pre_gateway_dispatch', event=event, gateway=object())
    rewrite = next(row for row in results if isinstance(row, dict) and row.get('action') == 'rewrite')
    command, token = rewrite['text'].lstrip('/').split(' ', 1)
    result = await manager._plugin_commands[command]['handler'](token)
    transcript.append({'member': text, 'assistant': result})
    return result

async def run():
    assert '起什么名字' in await say('你好', '1')
    assistant = '小迈' if profile == 'fixture-a' else '小医'
    address = '陈老师' if profile == 'fixture-a' else '李老师'
    assert '怎么称呼你' in await say(assistant, '2')
    menu = await say(address, '3')
    assert '医疗前沿频道' in menu
    assert ('AI 决策雷达' in menu) == (profile == 'fixture-a')
    result = await say('1,2' if profile == 'fixture-a' else '1', '4')
    assert '已订阅' in result and '操作指南' in result and '天气城市改为合肥' in result
    assert (home / 'config.yaml').read_bytes() == config_before
    assert assistant in (home / 'SOUL.md').read_text('utf-8') and address in (home / 'SOUL.md').read_text('utf-8')
    assert '保留原有助手规则' in (home / 'SOUL.md').read_text('utf-8')
    from agent.prompt_builder import load_soul_md
    native_prompt = load_soul_md(home_override=home)
    assert assistant in native_prompt and address in native_prompt
    assert (root / 'SOUL.md').read_text('utf-8') == template_soul
    request = Request(base + '/api/v1/channel-onboarding/catalog', data=json.dumps({'profile': profile,
        'recipient_id': peer}).encode(), headers={'Content-Type': 'application/json', 'X-SignalDesk-Key': 'synthetic-onboarding-service'})
    with urlopen(request, timeout=8) as response:
        state = json.load(response)
    expected = {'ai', 'medical', 'admin-existing'} if profile == 'fixture-a' else {'medical'}
    assert set(state['subscribed_channel_ids']) == expected
    # Exercise the actual delivered nickname wrapper after its native hot-reload path.
    prefix_path = home / 'plugins/channel-nickname-prefix/__init__.py'
    p_spec = importlib.util.spec_from_file_location('native_prefix_probe', prefix_path)
    prefix = importlib.util.module_from_spec(p_spec); p_spec.loader.exec_module(prefix)
    class Adapter:
        _hermes_home = str(home)
        async def send(self, chat_id, content, **kwargs): return content
        async def connect(self): return True
    prefix._patch_send(Adapter, 'weixin', '姐，', ('姐',))
    assert await Adapter().send(peer, '已完成') == address + '，已完成'
    assert (await say('操作指南', '5')).startswith('操作指南')
    evidence.mkdir(parents=True, exist_ok=True)
    report = {'passed': True, 'profile': profile, 'native_plugin_registration': True, 'native_hook_command': True,
        'native_default_clone': True, 'native_persona_loader': True,
        'real_http_subscription': True, 'permissions_unchanged': True, 'messages_sent_to_wechat': 0,
        'database': 'isolated synthetic SQLite', 'transcript': transcript}
    (evidence / (profile + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'transcript'}))

asyncio.run(run())
