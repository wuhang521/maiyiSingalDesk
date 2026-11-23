"""Keep first-contact collection behind the native private-message admission path."""
import asyncio
import json
from pathlib import Path
import secrets
import threading
import time

try:
    from . import onboarding
    from .onboarding_api import OnboardingApi
except ImportError:
    import onboarding
    from onboarding_api import OnboardingApi

_PENDING = {}
_LOCK = threading.Lock()
COMMANDS = {'入职引导', '操作指南', '使用指南', '帮助', '频道订阅'}


def profile_name(home):
    return home.name if home.parent.name == 'profiles' else 'default'


def wechat_owner(home):
    from dotenv import dotenv_values
    account = dotenv_values(home / '.env', interpolate=False).get('WEIXIN_ACCOUNT_ID')
    if not account or Path(account).name != account:
        return None
    path = home / 'weixin/accounts' / (account + '.json')
    onboarding._plain(path)
    return json.loads(path.read_text('utf-8')).get('user_id') if path.is_file() else None


def prepare(*, event, home):
    from hermes_cli.config import read_user_config_raw
    home = Path(home)
    if not read_user_config_raw(home / 'config.yaml').get('personal_onboarding', {}).get('enabled', False):
        return None
    source = getattr(event, 'source', None)
    if source is None or getattr(event, 'internal', False) or getattr(source, 'is_bot', False):
        return None
    platform = getattr(source, 'platform', '')
    peer = getattr(source, 'user_id', None)
    if (getattr(platform, 'value', platform) != 'weixin' or getattr(source, 'chat_type', '') != 'dm'
            or peer != getattr(source, 'chat_id', None) or not peer or peer != wechat_owner(home)):
        return None
    text = str(getattr(event, 'text', '') or '').strip()
    if text.startswith(('绑定迈医', '/')):
        return None
    try:
        state = onboarding.read_state(home)
    except (OSError, ValueError):
        # Keep the native reply path so the owner sees a Chinese error; do not reset data.
        state = None
    owned = state and state.get('owner') == onboarding.owner_key(profile_name(home), peer)
    if owned and (state['step'] == 'done' or state.get('paused')) and text not in COMMANDS:
        return None
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _LOCK:
        for old in [key for key, value in _PENDING.items() if value['deadline'] < now]:
            del _PENDING[old]
        _PENDING[token] = dict(home=home, peer=peer, text=text, profile=profile_name(home), deadline=now + 300,
                               message_id=getattr(event, 'message_id', None) or getattr(source, 'message_id', None))
    return {'action': 'rewrite', 'text': '/member-onboarding-result ' + token}


async def result(raw_args):
    from hermes_constants import get_hermes_home
    with _LOCK:
        pending = _PENDING.pop(str(raw_args or '').strip(), None)
    if not pending or pending['deadline'] < time.monotonic():
        return '入职引导请求已过期，请回复“入职引导”继续。'
    home = pending['home']
    if home != Path(get_hermes_home()) or wechat_owner(home) != pending['peer']:
        return '当前档案或微信绑定已变化，未保存个人设置，请使用当前绑定的微信重新发消息。'
    return await asyncio.to_thread(onboarding.handle, pending['text'], home=home, profile=pending['profile'],
        peer=pending['peer'], api=OnboardingApi(home), message_id=pending['message_id'])
