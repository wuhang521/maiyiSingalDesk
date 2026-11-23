"""Read-only post-deployment checks; never send or subscribe a real member."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from dotenv import dotenv_values

from hermes_cli.config import read_user_config_raw
from hermes_cli.profile_channels import channel_platforms_configured

root = Path('/opt/data'); rows = []
directory = root / 'plugins/shared-task-router'
sys.path.insert(0, str(directory))
import onboarding
import onboarding_gateway
from onboarding_api import OnboardingApi, ApiError

for name in ['default', 'chenying', 'futaotao', 'jiege', 'songwei', 'weixin-minjie', 'weixin-suzong', 'weixin-wuhang']:
    home = root if name == 'default' else root / 'profiles' / name
    config = read_user_config_raw(home / 'config.yaml')
    assert config['personal_onboarding']['enabled']
    platforms = channel_platforms_configured(home)
    env = dotenv_values(home / '.env', interpolate=False)
    # Native "configured" lists policy-only/disabled sections too; it is not an online status.
    if name == 'default':
        assert not platforms
    elif name == 'jiege':
        assert env.get('QQ_APP_ID') and env.get('QQ_CLIENT_SECRET')
        assert not any(env.get(k) for k in ['WEIXIN_ACCOUNT_ID', 'WEIXIN_TOKEN', 'WECOM_BOT_ID', 'WECOM_SECRET'])
    else:
        assert 'qqbot' not in platforms
    city = json.loads((home / 'personal-commute-weather.json').read_text())['location']['label']
    assert ('合肥' if name in {'chenying', 'futaotao'} else '北京') in city
    peer = onboarding_gateway.wechat_owner(home)
    if peer:
        event = SimpleNamespace(text='你好', source=SimpleNamespace(platform='weixin', chat_type='dm', user_id=peer, chat_id=peer))
        assert onboarding_gateway.prepare(event=event, home=home) is None
        try:
            snapshot = OnboardingApi(home).catalog(name, peer)
            api = {'catalog_http': 200, 'eligible_channels': len(snapshot['channels'])}
        except ApiError as error:
            if error.status != 404:
                raise
            api = {'catalog_http': 404, 'member_account_link_required': True}
    else:
        assert onboarding.read_state(home) is None
        api = {'catalog_http': 'not_applicable'}
    rows.append({'profile': name, 'notification_platform': None if name == 'default' else 'qqbot' if name == 'jiege' else 'weixin',
                 'city': city, 'old_member_not_reprompted': bool(peer), **api})
print(json.dumps({'passed': True, 'read_only': True, 'subscription_writes': 0, 'wechat_messages_sent': 0,
                  'profiles': rows}, ensure_ascii=False))
