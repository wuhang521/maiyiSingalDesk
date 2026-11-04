"""Synthetic activation transactions only: no SSH/systemctl/network/UI/config access.

The real filesystem under pytest tmp_path exercises absent-path atomic creation
and rollback. Systemd, Task/collector config and credentials are never invoked.
POSIX owner/mode assertions are separately marked; Windows is not POSIX evidence.
"""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('health_activation_under_test', Path(__file__).with_name('activate-health-schedule.py'))
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)
NOW = datetime(2026, 10, 30, 14, 0, tzinfo=tool.SHANGHAI)


def isolated_probe(tmp_path, probe):
    # -I -S deliberately excludes installed site packages: the producer must
    # run with only its existing src.main contract, never a new dotenv import.
    bootstrap = 'import sys;sys.path.insert(0,' + repr(str(tmp_path)) + ')\n'
    return subprocess.run([sys.executable, '-I', '-S', '-c', bootstrap + probe],
                          cwd=tmp_path, capture_output=True, text=True, timeout=15)


def test_real_producer_probe_subprocess_requires_no_dotenv_or_http_client(tmp_path):
    source = tmp_path / 'src'
    source.mkdir()
    (source / '__init__.py').write_text('')
    (source / 'main.py').write_text("from types import SimpleNamespace\n"
        "def load_config(path):\n"
        " assert path.as_posix()=='/opt/signaldesk/.env'\n"
        " return SimpleNamespace(ntfy_server='https://example.invalid',recipients=[SimpleNamespace(key='health',topic='fixture')])\n")
    reply = isolated_probe(tmp_path, tool.COLLECTOR_PROBE)
    assert reply.returncode == 0, reply.stderr
    result = json.loads(reply.stdout)
    assert set(result) == {'destination_sha256'} and tool.valid_sha(result['destination_sha256'])


def task_guard_probe_contract(tmp_path, flag):
    app = tmp_path / 'app'
    app.mkdir()
    (app / '__init__.py').write_text('')
    (app / 'config.py').write_text("from types import SimpleNamespace\ndef get_settings():return SimpleNamespace(api_key='offline-test-key')\n")
    (app / 'health_delivery.py').write_text("from types import SimpleNamespace\nfrom datetime import date\n"
        "class Config:\n @classmethod\n def load(cls,path):\n"
        "  assert path.as_posix()=='/etc/signaldesk/health-delivery.env'\n"
        "  return SimpleNamespace(server='https://example.invalid',topic='fixture',max_items=100,start_date=date(2026,10,31))\n")
    (tmp_path / 'dotenv.py').write_text("def dotenv_values(path,interpolate):\n"
        " assert path.as_posix()=='/opt/signaldesk/.env' and interpolate is False\n"
        " return {'SIGNALDESK_HEALTH_MANAGED':" + repr(flag) + "}\n")
    (tmp_path / 'httpx.py').write_text("class Response:\n"
        " def raise_for_status(self):pass\n"
        " def json(self):return [{'id':'health','managed':True,'readViaApi':True,'state':'active','schedule':'09:00','maxItems':100,'aliases':['demo-health'],'server':'https://example.invalid','topic':'fixture'}]\n"
        "class Client:\n"
        " def __init__(self,**kw):\n"
        "  assert kw['base_url']=='http://127.0.0.1:19080' and kw['headers']=={'X-SignalDesk-Key':'offline-test-key'}\n"
        " def __enter__(self):return self\n"
        " def __exit__(self,*args):pass\n"
        " def get(self,path):\n"
        "  assert path=='/api/v1/channels'\n"
        "  return Response()\n")
    reply = isolated_probe(tmp_path, tool.TASK_PROBE)
    if flag in (None, '1'):
        assert reply.returncode == 0, reply.stderr
        result = json.loads(reply.stdout)
        assert result.get('legacy_env_guard_conflict') is False
    else:
        assert reply.returncode != 0 and reply.stdout == ''


class FakeSystem:
    def __init__(self):
        self.units = {u: {'enabled': 'disabled', 'active': False} for u in (*tool.TIMERS, *tool.SERVICES)}
        self.units[tool.LEGACY] = {'enabled': 'enabled', 'active': True}
        self.initial = deepcopy(self.units)
        self.actions = []
        self.hook = None
        self.fail_once = None
        self.calendar_failure = False

    def state(self, unit):
        return dict(self.units[unit])

    def reload(self):
        self.actions.append(('daemon-reload', None))

    def guard_directive(self):
        return tool.DROPIN.is_file() and tool.DROPIN.read_bytes() == tool.DROPIN_BYTES

    def action(self, verb, unit):
        self.actions.append((verb, unit))
        if self.hook:
            self.hook(verb, unit)
        if self.fail_once == (verb, unit):
            self.fail_once = None
            raise RuntimeError('synthetic_command_failure')
        if verb in {'enable', 'disable'}:
            self.units[unit]['enabled'] = 'enabled' if verb == 'enable' else 'disabled'
        else:
            self.units[unit]['active'] = verb != 'stop'

    def calendar(self, timer, expected_time, now):
        if self.calendar_failure:
            raise tool.Refused('actual_calendar_not_verified')
        return {'on_calendar': '*-*-* ' + expected_time, 'next_calendar_at': '2026-10-31T' + expected_time + '+08:00', 'source_property': 'TimersCalendar.next_elapse'}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    task, collector, system, backups, acceptance, state = [tmp_path / x for x in ('task', 'collector', 'system', 'backups', 'varlib', 'state')]
    for directory in (task / 'app', collector / 'src', system, backups, acceptance, state):
        directory.mkdir(parents=True, mode=0o700)
    config = tmp_path / 'private.env'
    config.write_bytes(b'SYNTHETIC_CONFIG_NOT_A_CREDENTIAL=True\n')
    after = {}
    for key in tool.SOURCE_KEYS:
        value = ('SYNTHETIC_' + Path(key).stem.upper() + '=True\n').encode()
        (task / key).write_bytes(value)
        after[key] = tool.sha(value)
    guard = collector / 'src/main.py'
    guard.write_bytes(b'SYNTHETIC_GUARD=True\n')
    units = {}
    for name in tool.UNIT_SHA:
        raw = ('# synthetic ' + name + '\n').encode()
        (system / name).write_bytes(raw)
        units[name] = tool.sha(raw)
    for key, value in [('TASK', task), ('COLLECTOR', collector), ('SYSTEM', system), ('BACKUPS', backups),
                       ('ACCEPTANCE', acceptance), ('STATE', state), ('PRIVATE_CONFIG', config),
                       ('DROPIN', system / 'signaldesk-worker.service.d/health-managed.conf'),
                       ('GUARD_SHA', tool.sha(guard.read_bytes())), ('UNIT_SHA', units)]:
        monkeypatch.setattr(tool, key, value)
    for path in [config, guard, *(task / x for x in after)]:
        path.chmod(0o600)
    for name in units:
        (system / name).chmod(0o644)
    monkeypatch.setattr(tool, 'owner', lambda *args: None)
    monkeypatch.setattr(tool, 'sync_dir', lambda *args: None)
    monkeypatch.setattr(tool, 'exclusive_system', lambda: nullcontext())
    monkeypatch.setattr(tool, 'manager_time', lambda: None)
    monkeypatch.setattr(tool, 'configuration', lambda: {'canonical': 'health', 'aliases': ['demo-health'], 'schedule': '09:00', 'max_items': 100, 'start_date': tool.START_DATE, 'destination_sha256': 'a' * 64})
    # POSIX deployment metadata checks use real UID0 in CI; locally Windows is
    # not a proof of Linux UID/mode. No product path/permissions are touched.
    system_adapter = FakeSystem()
    return SimpleNamespace(task=task, collector=collector, system=system, backups=backups,
        acceptance=acceptance, state=state, config=config, after=after, adapter=system_adapter)


def evidence(tree):
    stage = tree.acceptance / 'signaldesk-health-timer-acceptance-synthetic-12345678'
    stage.mkdir(mode=0o700)
    document = {'schema': 'signaldesk.health-timer-acceptance.v1', 'passed': True, 'phase': 'verified',
        'state_dir': str(stage), 'test_run': True, 'clock_override': False, 'synthetic_only': False,
        'manager_timezone': 'Asia/Shanghai', 'ntp_synchronized': True,
        'scheduled_at': '2026-10-30T13:59:00+08:00', 'timer': {'last_trigger_monotonic_usec': 1000},
        'service': {'result': 'success', 'exec_main_status': 0},
        'first_delivery': {'state': 'sent', 'monotonic_usec': 1100, 'articles': 1, 'batch_rows': 1, 'ack_count': 1, 'attempts_total': 1, 'ingest_calls': 1, 'publish_calls': 1, 'ack_sha256': 'b' * 64},
        'second_delivery': {'state': 'sent', 'monotonic_usec': 1200, 'articles': 1, 'batch_rows': 1, 'ack_count': 1, 'attempts_total': 1, 'ingest_calls': 0, 'publish_calls': 0, 'ack_sha256': 'b' * 64},
        'dedupe_verified': True, 'source_sha256': tree.after,
        'artifact_sha256': {'runner': 'c' * 64, 'unit': 'd' * 64, 'timer': 'e' * 64}}
    path = stage / 'result.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    path.chmod(0o600)
    client = stage / 'client-reviewed.json'
    client.write_text('{"fixture":"manual receipt is separately reviewed by Root"}')
    client.chmod(0o600)
    return path, client, document


def ready_args(tree):
    path, client, _ = evidence(tree)
    return dict(system=tree.adapter, now=NOW, timer=path, timer_sha=tool.sha(path.read_bytes()),
                client=client, client_sha=tool.sha(client.read_bytes()), client_ui_reviewed=True)


def assert_restored(tree):
    assert tree.adapter.units == tree.adapter.initial
    assert not tool.DROPIN.exists()
    assert not tool.DROPIN.parent.exists()


def test_default_plan_no_writes_or_service_actions_with_missing_acceptance(tree):
    before = sorted(str(p.relative_to(tree.system)) for p in tree.system.rglob('*'))
    result = tool.activate(tree.after, system=tree.adapter, now=NOW)
    assert result['passed'] and result['mode'] == 'plan' and not result['activation_ready']
    assert result['missing_evidence'] == ['real_timer_acceptance', 'reviewed_client_receipt']
    assert result['service_actions'] == result['direct_messages_sent'] == 0
    assert result['client_ui_automatically_verified'] is False
    assert not tree.adapter.actions and not list(tree.backups.iterdir())
    assert sorted(str(p.relative_to(tree.system)) for p in tree.system.rglob('*')) == before


def test_reviewed_plan_is_ready_but_does_not_write(tree):
    result = tool.activate(tree.after, **ready_args(tree))
    assert result['activation_ready'] and result['passed'] and result['mode'] == 'plan'
    assert not tree.adapter.actions and not tool.DROPIN.exists() and not list(tree.backups.iterdir())


def test_activate_orders_guard_restart_enable_then_start_and_records_calendar(tree):
    result = tool.activate(tree.after, apply=True, **ready_args(tree))
    assert result['passed'] and result['activated'] and result['cutover_ledger_unchanged']
    assert tool.DROPIN.read_bytes() == tool.DROPIN_BYTES
    actions = tree.adapter.actions
    assert actions.index(('restart', tool.LEGACY)) < actions.index(('enable', tool.TIMERS[0]))
    assert max(actions.index(('enable', t)) for t in tool.TIMERS) < min(actions.index(('start', t)) for t in tool.TIMERS)
    assert result['calendars'][tool.TIMERS[1]]['on_calendar'] == '*-*-* 09:00:00'
    assert result['client_ui_automatically_verified'] is False
    saved = Path(result['backup'])
    assert json.loads((saved / 'before.json').read_text())['units'] == tree.adapter.initial
    assert json.loads((saved / 'result.json').read_text())['activated'] is True
    if os.name == 'posix':
        assert stat.S_IMODE(saved.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in saved.iterdir())


def test_successful_second_activation_does_not_restart_legacy_or_touch_files(tree):
    args = ready_args(tree)
    first = tool.activate(tree.after, apply=True, **args)
    before = tool.read_file(tool.DROPIN)[1]
    actions = list(tree.adapter.actions)
    backups = sorted(tree.backups.iterdir())
    second = tool.activate(tree.after, apply=True, **args)
    assert first['passed'] and second['passed'] and second['already_active']
    assert second['service_actions'] == 0 and tree.adapter.actions == actions
    assert tool.read_file(tool.DROPIN)[1] == before and sorted(tree.backups.iterdir()) == backups


def test_exact_existing_dropin_is_backed_up_and_preserved_on_failure(tree):
    args = ready_args(tree)
    tool.DROPIN.parent.mkdir()
    tool.DROPIN.write_bytes(tool.DROPIN_BYTES)
    tool.DROPIN.chmod(0o644)
    before = tool.read_file(tool.DROPIN)[1]
    tree.adapter.calendar_failure = True
    result = tool.activate(tree.after, apply=True, **args)
    assert not result['passed'] and result['rollback_verified']
    assert tool.read_file(tool.DROPIN)[1] == before
    old = json.loads((Path(result['backup']) / 'old-dropin.json').read_text())
    assert old['content'].encode() == tool.DROPIN_BYTES and old['metadata'] == before
    assert tree.adapter.units == tree.adapter.initial


def test_failure_immediately_after_atomic_create_removes_only_owned_new_file(tree, monkeypatch):
    args = ready_args(tree)
    failed = False
    def sync_once(path):
        nonlocal failed
        if path == tool.DROPIN.parent and not failed:
            failed = True
            raise OSError('synthetic directory sync failure after link')
    monkeypatch.setattr(tool, 'sync_dir', sync_once)
    result = tool.activate(tree.after, apply=True, **args)
    assert not result['passed'] and result['rollback_verified']
    assert_restored(tree)


@pytest.mark.parametrize('operation', [('restart', tool.LEGACY), ('enable', tool.TIMERS[0]), ('enable', tool.TIMERS[1]), ('start', tool.TIMERS[0]), ('start', tool.TIMERS[1])])
def test_partial_systemctl_failures_restore_before_state_and_absent_dropin(tree, operation):
    args = ready_args(tree)
    tree.adapter.fail_once = operation
    result = tool.activate(tree.after, apply=True, **args)
    assert not result['passed'] and result['rollback_verified'] and not result['rollback_errors']
    assert_restored(tree)


def test_calendar_validation_failure_rolls_back_everything(tree):
    args = ready_args(tree)
    tree.adapter.calendar_failure = True
    result = tool.activate(tree.after, apply=True, **args)
    assert not result['passed'] and result['error_code'] == 'actual_calendar_not_verified'
    assert result['rollback_verified']
    assert_restored(tree)


def test_third_image_dropin_is_preserved_and_legacy_not_restarted_again(tree):
    args = ready_args(tree)
    foreign = b'[Service]\n# external operator third image\n'
    def change(verb, unit):
        if (verb, unit) == ('start', tool.TIMERS[1]):
            tool.DROPIN.write_bytes(foreign)
    tree.adapter.hook = change
    result = tool.activate(tree.after, apply=True, **args)
    assert not result['passed'] and not result['rollback_verified']
    assert tool.DROPIN.read_bytes() == foreign
    assert tree.adapter.units == tree.adapter.initial
    assert tree.adapter.actions.count(('restart', tool.LEGACY)) == 1
    assert any(x['code'] == 'dropin_after_image_conflict' for x in result['rollback_errors'])


def test_existing_conflicting_dropin_is_refused_before_actions(tree):
    tool.DROPIN.parent.mkdir()
    tool.DROPIN.write_bytes(b'UNRELATED=True\n')
    with pytest.raises(tool.Refused, match='existing_dropin_conflict'):
        tool.activate(tree.after, system=tree.adapter, now=NOW)
    assert tool.DROPIN.read_bytes() == b'UNRELATED=True\n' and not tree.adapter.actions


@pytest.mark.parametrize('target', ['worker', 'collector', 'guard', 'unit'])
def test_frozen_code_and_unit_hash_guards_refuse_drift(tree, target):
    path = {'worker': tree.task / tool.SOURCE_KEYS[0], 'collector': tree.task / tool.SOURCE_KEYS[1],
            'guard': tree.collector / 'src/main.py', 'unit': tree.system / next(iter(tool.UNIT_SHA))}[target]
    path.write_bytes(b'THIRD_IMAGE=True\n')
    with pytest.raises(tool.Refused, match='deployed_source_sha256_mismatch'):
        tool.activate(tree.after, system=tree.adapter, now=NOW)
    assert not tree.adapter.actions and not tool.DROPIN.exists()


def test_activation_requires_client_review_flag_and_hash_bound_receipt(tree):
    args = ready_args(tree)
    args['client_ui_reviewed'] = False
    with pytest.raises(tool.Refused, match='acceptance_evidence_required_before_activation'):
        tool.activate(tree.after, apply=True, **args)
    assert not tree.adapter.actions and not list(tree.backups.iterdir())
    args['client_ui_reviewed'], args['client_sha'] = True, 'f' * 64
    with pytest.raises(tool.Refused, match='client_receipt_sha256_mismatch'):
        tool.activate(tree.after, apply=True, **args)


@pytest.mark.parametrize('mutation', ['synthetic', 'not_test_run', 'clock_override', 'future', 'no_trigger', 'service_error', 'two_acks', 'second_publish', 'second_ack', 'source', 'failed_state', 'second_before_first'])
def test_timer_evidence_cannot_replace_real_trigger_ack_and_dedupe(tree, mutation):
    path, client, document = evidence(tree)
    if mutation == 'synthetic': document['synthetic_only'] = True
    if mutation == 'not_test_run': document['test_run'] = False
    if mutation == 'clock_override': document['clock_override'] = True
    if mutation == 'future': document['scheduled_at'] = (NOW + timedelta(minutes=1)).isoformat()
    if mutation == 'no_trigger': document['timer']['last_trigger_monotonic_usec'] = 0
    if mutation == 'service_error': document['service']['exec_main_status'] = 1
    if mutation == 'two_acks': document['second_delivery']['ack_count'] = 2
    if mutation == 'second_publish': document['second_delivery']['publish_calls'] = 1
    if mutation == 'second_ack': document['second_delivery']['ack_sha256'] = '0' * 64
    if mutation == 'source': document['source_sha256'] = {**tree.after, tool.SOURCE_KEYS[0]: 'f' * 64}
    if mutation == 'failed_state': document['second_delivery']['state'] = 'unknown'
    if mutation == 'second_before_first': document['second_delivery']['monotonic_usec'] = 900
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(tool.Refused):
        tool.activate(tree.after, system=tree.adapter, now=NOW, timer=path, timer_sha=tool.sha(path.read_bytes()))
    assert not tree.adapter.actions and not list(tree.backups.iterdir())


def test_config_or_source_drift_during_activation_forces_rollback(tree):
    args = ready_args(tree)
    def change(verb, unit):
        if (verb, unit) == ('start', tool.TIMERS[1]):
            tree.config.write_bytes(b'EXTERNAL_CONFIG_CHANGED=True\n')
    tree.adapter.hook = change
    result = tool.activate(tree.after, apply=True, **args)
    assert not result['passed'] and result['error_code'] == 'source_changed_after_activation'
    assert result['rollback_verified'] and tree.config.read_bytes() == b'EXTERNAL_CONFIG_CHANGED=True\n'
    assert_restored(tree)


def test_success_evidence_write_failure_also_restores_runtime(tree, monkeypatch):
    args = ready_args(tree)
    real = tool.private_json
    def fail_result(path, data):
        if path.name == 'result.json':
            raise OSError('synthetic evidence failure')
        return real(path, data)
    monkeypatch.setattr(tool, 'private_json', fail_result)
    result = tool.activate(tree.after, apply=True, **args)
    assert not result['passed'] and result['rollback_verified']
    assert_restored(tree)


def test_cutover_ledger_drift_is_not_reported_no_send(tree):
    args = ready_args(tree)
    def unexpected_ledger(verb, unit):
        if (verb, unit) == ('start', tool.TIMERS[1]):
            (tree.state / 'ledger.sqlite3').write_bytes(b'EXTERNAL_LEDGER_DATA')
    tree.adapter.hook = unexpected_ledger
    result = tool.activate(tree.after, apply=True, **args)
    assert not result['passed'] and result['error_code'] == 'cutover_ledger_changed'
    assert 'cutover_ledger_unchanged' not in result and result['rollback_verified']
    assert (tree.state / 'ledger.sqlite3').read_bytes() == b'EXTERNAL_LEDGER_DATA'


@pytest.mark.parametrize('zone,ntp', [('UTC', 'yes'), ('Asia/Shanghai', 'no'), ('Asia/Shanghai', 'yes')])
def test_manager_time_gate_uses_verified_shanghai_and_ntp(monkeypatch, zone, ntp):
    monkeypatch.setattr(tool, 'command', lambda *a, **k: SimpleNamespace(stdout=f' Time zone: {zone} (CST, +0800)\n NTP synchronized: {ntp}\n'))
    if zone == 'Asia/Shanghai' and ntp == 'yes': tool.manager_time()
    else:
        with pytest.raises(tool.Refused, match='timezone_or_ntp_gate'): tool.manager_time()


def test_real_calendar_parser_does_not_accept_monotonic_retry_as_nine(monkeypatch):
    def unavailable(parts, **kwargs):
        if parts[:2] == ['busctl', 'call']:
            return SimpleNamespace(stdout='o "/org/freedesktop/systemd1/unit/signaldesk_2dhealth_2ddeliver_2etimer"\n')
        return SimpleNamespace(stdout='NextElapseUSecMonotonic=2min\n')
    monkeypatch.setattr(tool, 'command', unavailable)
    with pytest.raises(tool.Refused, match='actual_calendar_not_verified'):
        tool.System().calendar(tool.TIMERS[1], '09:00:00', NOW)
    def typed(parts, **kwargs):
        if parts[:2] == ['busctl', 'call']:
            return SimpleNamespace(stdout='o "/org/freedesktop/systemd1/unit/signaldesk_2dhealth_2ddeliver_2etimer"\n')
        assert parts[-2:] == ['org.freedesktop.systemd1.Timer', 'TimersCalendar']
        return SimpleNamespace(stdout='a(sst) 1 "OnCalendar" "*-*-* 09:00:00" ' + str(int(datetime(2026,10,31,9,tzinfo=tool.SHANGHAI).timestamp()*1000000)))
    monkeypatch.setattr(tool, 'command', typed)
    result = tool.System().calendar(tool.TIMERS[1], '09:00:00', NOW)
    assert result['next_calendar_at'] == '2026-10-31T09:00:00+08:00'


@pytest.mark.parametrize('kind', ['zero','infinity','past','wrong_time','wrong_day','wrong_base','wrong_spec','wrong_signature','extra_entry'])
def test_typed_calendar_rejects_unarmed_monotonic_wrong_date_or_shape(monkeypatch, kind):
    stamp = int(datetime(2026,10,31,9,tzinfo=tool.SHANGHAI).timestamp()*1000000)
    values = {'zero':0,'infinity':2**64-1,'past':int(NOW.timestamp()*1000000),
              'wrong_time':stamp+60000000,'wrong_day':stamp+86400000000}
    stamp = values.get(kind, stamp)
    signature = 'a(stt)' if kind=='wrong_signature' else 'a(sst)'
    base = 'OnUnitInactiveSec' if kind=='wrong_base' else 'OnCalendar'
    expression = '*-*-* 08:15:00' if kind=='wrong_spec' else '*-*-* 09:00:00'
    reply = signature + ' 1 "' + base + '" "' + expression + '" ' + str(stamp)
    if kind=='extra_entry':reply='a(sst) 2 "OnCalendar" "*-*-* 09:00:00" '+str(stamp)+' "OnCalendar" "*-*-* 09:00:00" '+str(stamp)
    def fake(parts, **kwargs):
        if parts[:2] == ['busctl','call']:
            return SimpleNamespace(stdout='o "/org/freedesktop/systemd1/unit/signaldesk_2dhealth_2ddeliver_2etimer"\n')
        return SimpleNamespace(stdout=reply)
    monkeypatch.setattr(tool,'command',fake)
    with pytest.raises(tool.Refused): tool.System().calendar(tool.TIMERS[1],'09:00:00',NOW)


@pytest.mark.parametrize('loaded,active,expected,error', [
    ('loaded', 'inactive', False, None), ('loaded', 'active', True, None),
    ('loaded', 'failed', False, None), ('not-found', 'inactive', None, 'unit_not_loaded'),
    ('loaded', 'unknown', None, 'unsupported_active_state'),
])
def test_systemd219_state_requires_authoritative_loaded_active_property(monkeypatch, loaded, active, expected, error):
    def reply(parts, **kwargs):
        if parts[:2] == ['systemctl', 'is-enabled']:
            return SimpleNamespace(stdout='disabled\n')
        if parts[:2] == ['systemctl', 'is-active']:
            return SimpleNamespace(stdout='unknown\n')  # Actual v219 inactive/GC response.
        assert parts[:2] == ['systemctl', 'show']
        return SimpleNamespace(stdout='LoadState=' + loaded + '\nActiveState=' + active + '\n')
    monkeypatch.setattr(tool, 'command', reply)
    if error:
        with pytest.raises(tool.Refused, match=error): tool.System().state(tool.TIMERS[0])
    else:
        assert tool.System().state(tool.TIMERS[0]) == {'enabled': 'disabled', 'active': expected}


def test_new_dropin_no_replace_preserves_concurrent_existing_file(tree):
    tool.DROPIN.parent.mkdir()
    foreign = b'EXTERNAL_OPERATOR=True\n'
    tool.DROPIN.write_bytes(foreign)
    with pytest.raises(tool.Refused, match='dropin_created_concurrently'):
        tool.create_dropin({})
    assert tool.DROPIN.read_bytes() == foreign


@pytest.mark.parametrize('flag', [None, '1', '', '0', 'false'])
def test_private_environment_file_cannot_override_managed_guard(tmp_path, flag):
    # Retain the original five guard cases, now executing the complete Task
    # probe in a real isolated subprocess instead of injecting sys.modules.
    task_guard_probe_contract(tmp_path, flag)
