"""Clone the actual production default with native code into an isolated directory."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

root = Path('/opt/data')
assert os.environ.get('HERMES_HOME', '/opt/data') == str(root)
os.environ['HERMES_HOME'] = str(root)
from hermes_cli import profiles
from hermes_cli.config import read_user_config_raw
from hermes_cli.profile_channels import channel_platforms_configured
from hermes_constants import get_hermes_home

protected = [root / name for name in ['config.yaml', '.env', 'SOUL.md', 'personal-commute-weather.json']]
before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
config = read_user_config_raw(root / 'config.yaml')
assert config['personal_onboarding']['enabled'] and not channel_platforms_configured(root)
assert not (root / 'personal-onboarding.json').exists()
assert '北京' in config['personal_commute_weather']['default_location']['label']

with tempfile.TemporaryDirectory(prefix='onboarding-default-clone-', dir='/tmp') as temporary:
    isolated = Path(temporary)
    # Only redirect the destination registry and suppress service activation; copy code is native.
    with patch.object(profiles, '_get_profiles_root', return_value=isolated / 'profiles'), \
         patch.object(profiles, '_maybe_register_gateway_service'), patch.object(profiles, '_notify_multiplexer'):
        clone = profiles.create_profile('onboarding-fixture', clone_from='default', clone_config=True, no_alias=True)
    assert clone.is_relative_to(isolated) and not channel_platforms_configured(clone)
    assert not (clone / 'personal-onboarding.json').exists()
    assert not any(p.is_file() for name in ['weixin', 'pairing'] for p in (clone / name).rglob('*'))
    copied = read_user_config_raw(clone / 'config.yaml')
    for name in ['personal_onboarding', 'personal_commute_weather', 'weixin_recovery', 'model']:
        assert copied.get(name) == config.get(name), name
    folder = clone / 'plugins/shared-task-router'
    for name in ['__init__.py', 'onboarding.py', 'onboarding_gateway.py', 'onboarding_api.py', 'operations-guide.md']:
        assert (folder / name).read_bytes() == (root / 'plugins/shared-task-router' / name).read_bytes()
    os.environ['HERMES_HOME'] = str(clone)
    assert Path(get_hermes_home()) == clone
    path = clone / 'plugins/personal-commute-weather'
    spec = importlib.util.spec_from_file_location('native_template_weather', path / '__init__.py',
                                                submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    from native_template_weather.bootstrap import ensure_profile
    ensure_profile(clone)
    from native_template_weather import weather
    assert weather.preferred_target(clone) is None
    state = json.loads((clone / 'personal-commute-weather.json').read_text())
    assert not state.get('recipient_sha256') and not state.get('notification_platform')
    assert '北京' in state['location']['label']
    from cron.jobs import load_jobs
    jobs = [job for job in load_jobs() if job.get('script') in weather.SCRIPTS]
    assert len(jobs) == 2
    assert {job['schedule']['expr'] for job in jobs} == {'0 9 * * 1-5', '0 18 * * 1-5'}
    assert all(job['timezone'] == 'Asia/Shanghai' and job['state'] == 'paused' for job in jobs)
os.environ['HERMES_HOME'] = str(root)
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in before.items())
print(json.dumps({'passed': True, 'actual_production_default': True, 'native_clone': True,
    'onboarding_inherited': True, 'no_message_connections': True, 'no_personal_onboarding_state': True,
    'native_recovery_and_model_inherited': True, 'weather_city': '北京', 'weather_timezone': 'Asia/Shanghai',
    'weather_weekdays': ['09:00', '18:00'], 'weather_jobs_paused_until_bound': True,
    'production_default_unchanged': True, 'wechat_messages_sent': 0}, ensure_ascii=False))
