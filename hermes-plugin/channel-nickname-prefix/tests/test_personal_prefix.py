import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / '__init__.py'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class PersonalPrefixTests(unittest.TestCase):
    def test_fresh_process_registration_uses_native_platform_modules(self):
        class QQ:
            async def send(self, chat_id, content, **kwargs):
                return content
            async def connect(self):
                return True
        class Weixin:
            async def send(self, chat_id, content, **kwargs):
                return content
            async def connect(self):
                return True
        module = load('prefix_native_modules')
        native = {'plugins.platforms.qqbot.adapter': SimpleNamespace(QQAdapter=QQ),
                  'plugins.platforms.weixin.adapter': SimpleNamespace(WeixinAdapter=Weixin)}
        with patch.dict('sys.modules', native), patch.object(module, '_patch_gateway_lifecycle'):
            module.register(None)
        self.assertTrue(hasattr(QQ.send, '__wrapped__'))
        self.assertTrue(hasattr(Weixin.send, '__wrapped__'))

    def test_member_address_is_used_only_for_its_profile_and_private_receiver(self):
        module = load('prefix_fixture')
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'profiles' / 'new-member'; home.mkdir(parents=True)
            peer = 'person@im.wechat'
            owner = hashlib.sha256(json.dumps(['new-member', 'weixin', peer], ensure_ascii=False).encode()).hexdigest()
            (home / 'personal-onboarding.json').write_text(json.dumps({'schema': 1, 'owner': owner,
                'step': 'done', 'user_address': '陈老师'}), encoding='utf-8')
            adapter = SimpleNamespace(_hermes_home=str(home))
            class Adapter:
                _hermes_home = str(home)
                async def send(self, chat_id, content, **kwargs):
                    return content
                async def connect(self):
                    return True
            module._patch_send(Adapter, 'weixin', '姐，', ('姐',))
            self.assertEqual(asyncio.run(Adapter().send(peer, '测试')), '陈老师，测试')
            self.assertEqual(asyncio.run(Adapter().send('other@im.wechat', '测试')), '姐，测试')

    def test_native_hot_reload_replaces_only_its_own_wrapper_without_duplicate_prefix(self):
        class Adapter:
            async def send(self, chat_id, content, **kwargs):
                return content
            async def connect(self):
                return True
        first = load('prefix_before_reload')
        first._patch_send(Adapter, 'weixin', '旧称呼，', ('旧称呼',))
        second = load('prefix_after_reload')
        second._patch_send(Adapter, 'weixin', '新称呼，', ('新称呼',))
        self.assertEqual(asyncio.run(Adapter().send('peer', '测试')), '新称呼，测试')


if __name__ == '__main__':
    unittest.main()
