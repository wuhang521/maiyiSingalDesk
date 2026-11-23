import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))
spec = importlib.util.spec_from_file_location('onboarding_gateway_fixture', PLUGIN / 'onboarding_gateway.py')
gateway = importlib.util.module_from_spec(spec); spec.loader.exec_module(gateway)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'profiles' / 'member'; self.home.mkdir(parents=True)
        (self.home / '.env').write_text('WEIXIN_ACCOUNT_ID=synthetic-account\n', encoding='utf-8')
        folder = self.home / 'weixin/accounts'; folder.mkdir(parents=True)
        self.account = folder / 'synthetic-account.json'
        self.account.write_text(json.dumps({'user_id': 'member@im.wechat'}), encoding='utf-8')
        config = ModuleType('hermes_cli.config'); config.read_user_config_raw = lambda _: {'personal_onboarding': {'enabled': True}}
        constants = ModuleType('hermes_constants'); constants.get_hermes_home = lambda: self.home
        self.patch = patch.dict(sys.modules, {'hermes_cli.config': config, 'hermes_constants': constants})
        self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def event(self, **kwargs):
        source = SimpleNamespace(platform=SimpleNamespace(value='weixin'), user_id='member@im.wechat',
                                 chat_id='member@im.wechat', chat_type='dm', is_bot=False)
        for key, value in kwargs.items():
            setattr(source, key, value)
        return SimpleNamespace(text='你好', source=source, message_id='fixture-message', internal=False)

    def test_group_another_person_and_internal_message_cannot_start_personal_collection(self):
        for event in (self.event(chat_type='group'), self.event(user_id='stranger@im.wechat'), self.event(is_bot=True)):
            self.assertIsNone(gateway.prepare(event=event, home=self.home))
        event = self.event(); event.internal = True
        self.assertIsNone(gateway.prepare(event=event, home=self.home))
        self.assertFalse((self.home / 'personal-onboarding.json').exists())

    def test_native_rewrite_and_command_collect_only_after_owner_revalidation(self):
        rewrite = gateway.prepare(event=self.event(), home=self.home)
        self.assertEqual(rewrite['action'], 'rewrite')
        self.assertFalse((self.home / 'personal-onboarding.json').exists())
        token = rewrite['text'].split()[1]
        result = asyncio.run(gateway.result(token))
        self.assertIn('起什么名字', result)
        self.assertEqual(json.loads((self.home / 'personal-onboarding.json').read_text('utf-8'))['step'], 'assistant')

    def test_rebind_during_native_admission_prevents_an_old_request_from_writing(self):
        rewrite = gateway.prepare(event=self.event(), home=self.home)
        self.account.write_text(json.dumps({'user_id': 'new-owner@im.wechat'}), encoding='utf-8')
        result = asyncio.run(gateway.result(rewrite['text'].split()[1]))
        self.assertIn('绑定已变化', result)
        self.assertFalse((self.home / 'personal-onboarding.json').exists())

    def test_corrupt_onboarding_is_reported_instead_of_silently_reset(self):
        (self.home / 'personal-onboarding.json').write_text('{broken', encoding='utf-8')
        rewrite = gateway.prepare(event=self.event(), home=self.home)
        result = asyncio.run(gateway.result(rewrite['text'].split()[1]))
        self.assertIn('未完成保存', result)
        self.assertEqual((self.home / 'personal-onboarding.json').read_text('utf-8'), '{broken')


if __name__ == '__main__':
    unittest.main()
