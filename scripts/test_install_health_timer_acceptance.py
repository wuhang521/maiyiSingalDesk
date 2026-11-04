"""Synthetic local state/manager only; no systemctl/runuser/network is executed."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('timer_acceptance_tool', Path(__file__).with_name('install-health-timer-acceptance.py'))
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)
ROOT = Path(__file__).parents[1]


class Clock(datetime):
    current = datetime.fromisoformat('2026-10-30T10:30:00+08:00')
    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current


@pytest.fixture
def tree(tmp_path, monkeypatch):
    task, states, units = [tmp_path / value for value in ('task', 'states', 'units')]
    for path in (task / 'app', states, units): path.mkdir(parents=True)
    config = tmp_path / 'synthetic-config'
    config.write_bytes(b'SYNTHETIC CONFIG ONLY')
    (task / '.env').write_bytes(b'SYNTHETIC ENV ONLY')
    expected = {}
    for name in tool.MEMBERS:
        data = b'SYNTHETIC_PUBLIC_CODE = True\n'
        (task / name).write_bytes(data); expected[name] = tool.sha(data)
    for key, value in [('TASK', task), ('PYTHON', task / '.venv/bin/python'), ('CONFIG', config),
                       ('ENVIRONMENT', task / '.env'), ('STATE_PARENT', states), ('UNITS', units), ('datetime', Clock)]:
        monkeypatch.setattr(tool, key, value)
    Clock.current = datetime.fromisoformat('2026-10-30T10:30:00+08:00')
    commands = []
    def command(parts, **kwargs):
        commands.append(parts)
        if parts == ['systemctl', '--version']: return 'systemd 219\n'
        if parts == ['timedatectl', 'status']: return ' Time zone: Asia/Shanghai (CST, +0800)\n NTP synchronized: yes\n'
        return ''
    monkeypatch.setattr(tool, 'command', command)
    monkeypatch.setattr(tool.os, 'chown', lambda *args: None, raising=False)
    def portable_write(path, data, *metadata, planned=None):
        path.write_bytes(data)
        if planned is not None: planned[path.name] = tool.identity(path)
    monkeypatch.setattr(tool, 'write', portable_write)
    prepared = {'articles': 1, 'batches': 1, 'ingest_calls': 0, 'publish_calls': 0}
    monkeypatch.setattr(tool, 'run_as_task', lambda state, mode: prepared.copy())
    run_id = 'timer-unique-synthetic-001'
    state = tool.state_for(run_id)
    original = Path.lstat
    def metadata(path):
        info = original(path)
        values = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
        if path == state:
            values.update(st_uid=994, st_gid=990, st_mode=stat.S_IFDIR | 0o700)
        elif path in (state / 'manifest.json', state / 'runner.py') or path.parent == state / 'code':
            values.update(st_uid=0, st_gid=990, st_mode=stat.S_IFREG | 0o440)
        elif path.parent == units:
            values.update(st_uid=0, st_gid=0,
                st_mode=stat.S_IFREG | (0o644 if info.st_mode & stat.S_IWRITE else 0o444))
        return SimpleNamespace(**values)
    monkeypatch.setattr(Path, 'lstat', metadata)
    return SimpleNamespace(task=task, states=states, units=units, state=state, expected=expected,
                           run_id=run_id, commands=commands)


def installed(tree):
    return tool.install(tree.run_id, tree.expected, apply=True)


def manager(monkeypatch, trigger=100, success=True):
    monkeypatch.setattr(tool, 'timer_monotonic_usec', lambda unit: trigger)
    def properties(unit, names):
        return {'LastTriggerUSecMonotonic': str(trigger)} if unit.endswith('.timer') else {
            'Result': 'success' if success else 'failed', 'ExecMainStatus': '0' if success else '1', 'ActiveState': 'inactive'}
    monkeypatch.setattr(tool, 'properties', properties)


def test_systemd219_uses_typed_dbus_usec_without_parsing_formatted_months(monkeypatch):
    replies = iter(['o "/org/freedesktop/systemd1/unit/test_2etimer"\n', 't 3375406495946\n'])
    calls = []
    def command(parts):
        calls.append(parts); return next(replies)
    monkeypatch.setattr(tool, 'command', command)
    assert tool.timer_monotonic_usec('synthetic.timer') == 3375406495946
    assert calls[0][-3:] == ['GetUnit', 's', 'synthetic.timer']
    assert calls[1][-2:] == ['org.freedesktop.systemd1.Timer', 'LastTriggerUSecMonotonic']


@pytest.mark.parametrize('scalar', ['t 1month 1w 1d 15h 6min 46.495946s', 's "3375406495946"', 't 18446744073709551616'])
def test_formatted_wrong_type_or_overflow_monotonic_values_are_refused(monkeypatch, scalar):
    replies = iter(['o "/org/freedesktop/systemd1/unit/test_2etimer"\n', scalar])
    monkeypatch.setattr(tool, 'command', lambda parts: next(replies))
    with pytest.raises(tool.Refused, match='typed_timer_uint64_required'):
        tool.timer_monotonic_usec('synthetic.timer')


def receipt(index):
    return {'monotonic_usec': 200 + index, 'state': 'sent', 'articles': 1, 'batch_rows': 1, 'ack_count': 1,
            'attempts_total': 1, 'ingest_calls': 1 if index == 1 else 0,
            'publish_calls': 1 if index == 1 else 0, 'ack_sha256': 'a' * 64}


def save_receipts(tree, count=2):
    for index in range(1, count + 1):
        (tree.state / f'delivery-{index}.json').write_text(json.dumps(receipt(index)))
    Clock.current = datetime.fromisoformat('2026-10-30T10:34:00+08:00')


def test_default_plan_is_nonmutating_and_calendar_is_real_shanghai_plus120(tree):
    result = tool.install(tree.run_id, tree.expected)
    assert result['mode'] == 'dry_run' and not result['passed']
    assert result['clock_override'] is False and result['scheduled_at'] == '2026-10-30T10:32:00+08:00'
    assert not tree.state.exists() and not list(tree.units.iterdir())
    assert not any(parts[:2] == ['systemctl', 'start'] for parts in tree.commands)


def test_command_uses_fixed_task_cwd_and_scrubbed_environment(monkeypatch):
    captured = []
    def run(parts, **options):
        captured.append((parts, options))
        return SimpleNamespace(returncode=0, stdout='synthetic-safe-result')
    monkeypatch.setattr(tool.subprocess, 'run', run)
    assert tool.command(['systemctl', '--version']) == 'synthetic-safe-result'
    assert captured[0][1]['cwd'] == tool.TASK
    assert set(captured[0][1]['env']) == {'PATH', 'LANG', 'LC_ALL', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED'}


def test_install_prepares_without_network_then_starts_only_unique_timer(tree):
    result = installed(tree)
    assert result['phase'] == 'awaiting_timer' and not result['passed']
    starts = [parts for parts in tree.commands if parts[:2] == ['systemctl', 'start']]
    assert starts == [['systemctl', 'start', '--no-block', result['timer_unit']]]
    assert not any('enable' in parts or 'systemd-run' in parts for parts in tree.commands)
    service = (tree.units / result['service_unit']).read_text()
    timer = (tree.units / result['timer_unit']).read_text()
    assert 'User=994\nGroup=990\n' in service and 'EnvironmentFile=/opt/signaldesk-task/.env' in service
    assert '--deliver' in service and '--at' not in service
    assert 'OnCalendar=2026-10-30 10:32:00\nAccuracySec=1s' in timer
    assert result['prepare']['ingest_calls'] == result['prepare']['publish_calls'] == 0
    with pytest.raises(tool.Refused, match='new_path_required'):
        tool.install(tree.run_id, tree.expected, apply=True)


@pytest.mark.parametrize('instant', ['2026-10-30T08:00:00+08:00', '2026-10-30T23:59:00+08:00'])
def test_invalid_worker_time_window_is_refused(instant):
    with pytest.raises(tool.Refused, match='same_day_after0900'):
        tool.schedule(datetime.fromisoformat(instant))


def test_two_manual_records_cannot_replace_actual_timer_trigger(tree, monkeypatch):
    installed(tree); save_receipts(tree); manager(monkeypatch, trigger=0)
    result = tool.inspect(tree.run_id)
    assert not result['passed'] and result['phase'] == 'awaiting_timer'
    with pytest.raises(tool.Refused, match='real_timer_first_receipt_required'):
        tool.second(tree.run_id, apply=True)


def test_timer_then_second_receipt_exact_ledger_proves_dedupe(tree, monkeypatch):
    installed(tree); save_receipts(tree, count=1); manager(monkeypatch)
    result = tool.second(tree.run_id, apply=True)
    assert result['phase'] == 'awaiting_second' and result['second_start_requested']
    assert tree.commands[-1] == ['systemctl', 'start', '--no-block', result['service_unit']]
    (tree.state / 'delivery-2.json').write_text(json.dumps(receipt(2)))
    final = tool.inspect(tree.run_id)
    assert final['schema'] == 'signaldesk.health-timer-acceptance.v1'
    assert final['passed'] and final['phase'] == 'verified' and final['dedupe_verified']
    assert set(final['source_sha256']) == set(tool.MEMBERS)
    assert set(final['artifact_sha256']) == {'runner', 'unit', 'timer'}
    assert final['service']['result'] == 'success' and final['service']['exec_main_status'] == 0
    assert final['synthetic_only'] is False


@pytest.mark.parametrize('field,value', [('publish_calls', 1), ('ingest_calls', 1), ('batch_rows', 2), ('ack_sha256', 'b' * 64)])
def test_second_delivery_changes_block_verified_result(tree, monkeypatch, field, value):
    installed(tree); save_receipts(tree); manager(monkeypatch)
    second = receipt(2); second[field] = value
    (tree.state / 'delivery-2.json').write_text(json.dumps(second))
    assert not tool.inspect(tree.run_id)['passed']


def test_manifest_cannot_redirect_cleanup_to_production_units(tree, monkeypatch):
    result = installed(tree)
    manifest = json.loads((tree.state / 'manifest.json').read_text())
    manifest['service_unit'] = 'signaldesk-health-deliver.service'
    (tree.state / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(tool.Refused, match='fixed_test_units_required'):
        tool.cleanup(tree.run_id, apply=True)
    assert (tree.units / result['service_unit']).exists()
    assert not any(parts[:2] == ['systemctl', 'stop'] for parts in tree.commands)


def test_cleanup_only_removes_test_units_and_preserves_private_ledger(tree, monkeypatch):
    installed(tree); save_receipts(tree); manager(monkeypatch)
    ledger = tree.state / 'ledger-retention-test'; ledger.write_bytes(b'SYNTHETIC ONLY')
    result = tool.cleanup(tree.run_id, apply=True)
    assert result['test_units_removed'] and result['private_ledger_preserved']
    assert not list(tree.units.iterdir()) and ledger.read_bytes() == b'SYNTHETIC ONLY'


def test_same_bytes_new_unit_inode_is_retained_by_cleanup(tree, monkeypatch):
    result = installed(tree); save_receipts(tree); manager(monkeypatch)
    unit = tree.units / result['service_unit']
    before = unit.lstat().st_ino
    replacement = tree.units / 'synthetic-replacement'
    replacement.write_bytes(unit.read_bytes()); os.replace(replacement, unit)
    assert unit.lstat().st_ino != before
    with pytest.raises(tool.Refused, match='unit_afterimage_changed'):
        tool.cleanup(tree.run_id, apply=True)
    assert unit.exists() and not any(parts[:2] == ['systemctl', 'stop'] for parts in tree.commands)


def test_unit_permission_change_is_retained_by_cleanup(tree, monkeypatch):
    result = installed(tree); save_receipts(tree); manager(monkeypatch)
    unit = tree.units / result['timer_unit']; unit.chmod(0o444)
    try:
        with pytest.raises(tool.Refused, match='root_unit_metadata_required'):
            tool.cleanup(tree.run_id, apply=True)
        assert unit.exists() and not any(parts[:2] == ['systemctl', 'stop'] for parts in tree.commands)
    finally:
        unit.chmod(0o644)


def test_failed_test_cleanup_does_not_claim_pass_and_preserves_state(tree, monkeypatch):
    installed(tree); manager(monkeypatch, trigger=0, success=False)
    result = tool.cleanup(tree.run_id, apply=True)
    assert result['test_units_removed'] and not result['passed'] and tree.state.is_dir()


def test_lost_start_ack_stops_only_created_test_units_and_retains_state(tree, monkeypatch):
    command = tool.command
    def lost(parts, **kwargs):
        if parts[:3] == ['systemctl', 'start', '--no-block']:
            tree.commands.append(parts)
            raise tool.Refused('synthetic_lost_start_ack')
        return command(parts, **kwargs)
    monkeypatch.setattr(tool, 'command', lost)
    with pytest.raises(tool.Refused, match='lost_start_ack'):
        installed(tree)
    assert not list(tree.units.iterdir()) and tree.state.is_dir()
    stops = [parts for parts in tree.commands if parts[:2] == ['systemctl', 'stop']]
    assert len(stops) == 1 and all(name.startswith(tool.PREFIX + tree.run_id) for name in stops[0][2:])


@pytest.fixture
def runtime(tree, monkeypatch):
    installed(tree)
    namespace = {'__file__': str(tree.state / 'runner.py'), '__name__': 'synthetic_runner_unit'}
    exec(compile(tool.RUNNER, 'actual_embedded_runner', 'exec'), namespace)
    monkeypatch.syspath_prepend(str(ROOT / 'server'))
    from app import health_delivery as real
    monkeypatch.setattr(real, 'datetime', Clock)
    namespace['datetime'] = Clock
    monkeypatch.setattr(namespace['os'], 'getuid', lambda: 994, raising=False)
    monkeypatch.setattr(namespace['os'], 'getgid', lambda: 990, raising=False)
    config = real.Config('https://ntfy.synthetic.test', 'synthetic-health')
    monkeypatch.setattr(real.Config, 'load', lambda path: config)
    calls = []
    class LocalTransport:
        def __init__(self, config): pass
        def ingest(self, items): calls.append(('ingest', items))
        def publish(self, title, body):
            calls.append(('publish', title, body))
            return {'event': 'message', 'id': 'synthetic-ack', 'title': title, 'message': body}
        def cached(self, since): raise AssertionError('No cache lookup is expected for an exact ACK')
    monkeypatch.setattr(real, 'Transport', LocalTransport)
    return SimpleNamespace(namespace=namespace, real=real, calls=calls, config=config)


def test_actual_runner_prepare_then_real_worker_twice_uses_one_ack(runtime, tree):
    run = runtime.namespace['execute']
    prepared = run('prepare', runtime.real)
    assert prepared['articles'] == prepared['batches'] == 1 and runtime.calls == []
    first = run('deliver', runtime.real)
    second = run('deliver', runtime.real)
    assert first['state'] == second['state'] == 'sent'
    assert first['ingest_calls'] == first['publish_calls'] == 1
    assert second['ingest_calls'] == second['publish_calls'] == 0
    assert first['batch_rows'] == first['ack_count'] == first['attempts_total'] == 1
    assert all(second[name] == first[name] for name in ('ack_sha256', 'batch_rows', 'ack_count', 'attempts_total'))
    assert len(runtime.calls) == 2
    article = runtime.calls[0][1][0]
    assert article['title'] == runtime.calls[1][1]
    assert article['clickUrl'] is None and article['source'] == 'ntfy.synthetic.test'
    with pytest.raises(ValueError, match='two_deliveries_only'):
        run('deliver', runtime.real)


def test_runner_prepare_blocks_any_accidental_worker_network(runtime):
    import socket
    def bad_prepare(self):
        with socket.socket() as client:
            client.connect(('127.0.0.1', 1))
    original = runtime.real.Worker.prepare
    runtime.real.Worker.prepare = bad_prepare
    try:
        with pytest.raises(RuntimeError, match='prepare_network_forbidden'):
            runtime.namespace['execute']('prepare', runtime.real)
    finally:
        runtime.real.Worker.prepare = original
    assert runtime.calls == []


def test_actual_bundled_client_parser_matches_single_synthetic_item(runtime, tree):
    node = os.environ.get('HEALTH_CONTENT_CONTRACT_NODE')
    if not node: pytest.skip('Requires explicit local Node; no download/network fallback')
    runtime.namespace['execute']('prepare', runtime.real)
    snapshot = json.loads(next((tree.state / 'worker-state/snapshots').glob('*.json')).read_text(encoding='utf-8'))
    title = snapshot['articles'][0]['title']
    response = subprocess.run([node, str(ROOT / 'scripts/health-content-contract.cjs')], input=json.dumps({
        'channel': {'id': 'health', 'name': '健康管理商业情报', 'server': runtime.config.server},
        'bodies': snapshot['bodies'], 'time': snapshot['prepared_at'], 'title': title}),
        capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert response.returncode == 0
    result = json.loads(response.stdout)
    assert result['item_count'] == 1
    for name in ('title', 'body', 'source', 'clickUrl'):
        value = snapshot['articles'][0].get(name) or ''
        assert result['items'][0][name + '_sha256'] == hashlib.sha256(value.encode()).hexdigest()
