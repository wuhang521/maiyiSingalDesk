import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


def test_only_private_transport_identity_is_sent_to_the_claim_api(monkeypatch):
    spec = importlib.util.spec_from_file_location("signaldesk_wechat_pairing_test", Path(__file__).parents[1] / "__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, plugin)
    spec.loader.exec_module(plugin)
    claims = []

    def claim(*args):
        claims.append(args)
        return "verified"

    monkeypatch.setattr(plugin, "_submit_claim", claim)
    gateway = SimpleNamespace(_primary_profile_name="clinic")
    peer = "synthetic-pairing-peer@im.wechat"
    source = SimpleNamespace(platform="weixin", user_id=peer, chat_id=peer, chat_type="dm", profile="untrusted-runtime-name")
    code = "X" * 32

    def send(text):
        event = SimpleNamespace(text=text, source=source)
        rewrite = plugin._pre_gateway_dispatch(event=event, gateway=gateway)
        assert rewrite["action"] == "rewrite" and code not in rewrite["text"]
        return asyncio.run(plugin._result_command(rewrite["text"].split()[1]))

    assert plugin._pre_gateway_dispatch(event=SimpleNamespace(text="帮我建任务", source=source), gateway=gateway) is None
    assert send(f"绑定迈医 {code}") == "verified"
    assert "复制完整" in send(f"绑定迈医 {code} forged@im.wechat")
    source.chat_type = "group"
    assert "微信私聊" in send(f"绑定迈医 {code}")
    source.chat_type = "dm"
    source.chat_id = "unrelated-peer@im.wechat"
    assert "微信私聊" in send(f"绑定迈医 {code}")
    assert claims == [(code, peer, "clinic")]
