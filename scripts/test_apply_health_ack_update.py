"""Synthetic fixed-path single-file transactions; no production/SSH/network/UI."""
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('health_ack_update_under_test', Path(__file__).with_name('apply-health-ack-update.py'))
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)
REAL_COMPILE = tool.compile_only


@pytest.fixture
def tree(tmp_path, monkeypatch):
    app, backups, payloads = [tmp_path / name for name in ('app', 'backups', 'payloads')]
    for directory in (app, backups, payloads): directory.mkdir(mode=0o700)
    worker, collector = app / 'health_delivery.py', app / 'health_collector.py'
    old, original_collector = b'ORIGINAL_WORKER=True\n', b'CURRENT_COLLECTOR=True\n'
    worker.write_bytes(old); collector.write_bytes(original_collector)
    worker.chmod(0o640); collector.chmod(0o640)
    os.utime(worker, ns=(1670000000000000000, 1670000000000000000))
    for key, value in [('APP', app), ('WORKER', worker), ('COLLECTOR', collector), ('BACKUPS', backups),
                       ('PAYLOADS', payloads), ('BEFORE', tool.sha(old)), ('COLLECTOR_SHA', tool.sha(original_collector))]:
        monkeypatch.setattr(tool, key, value)
    monkeypatch.setattr(tool, 'owner', lambda *args: None)
    monkeypatch.setattr(tool, 'sync', lambda *args: None)
    monkeypatch.setattr(tool, 'locked', lambda: nullcontext())
    monkeypatch.setattr(tool, 'compile_only', lambda data: '3.10.19')
    payload = payloads / 'signaldesk-health-ack-synthetic.py'
    data = b'UPDATED_WORKER_WITH_ACK_TRIM=True\n'
    payload.write_bytes(data)
    return SimpleNamespace(app=app, backups=backups, payloads=payloads, worker=worker, collector=collector,
        old=old, collector_bytes=original_collector, before=tool.read(worker)[1], collector_image=tool.read(collector)[1],
        payload=payload, data=data, after=tool.sha(data))


def preserved(tree):
    assert tool.same(tree.collector, tree.collector_image)
    assert tree.collector.read_bytes() == tree.collector_bytes


def test_default_dry_run_has_zero_writes_and_only_compiles_in_memory(tree, monkeypatch):
    compiled = []
    monkeypatch.setattr(tool, 'compile_only', lambda data: compiled.append(data) or '3.10.19')
    result = tool.update(tree.payload, tree.after)
    assert result['passed'] and result['mode'] == 'dry_run'
    assert compiled == [tree.data] and tree.worker.read_bytes() == tree.old
    assert not list(tree.backups.iterdir())
    assert result['database_actions'] == result['env_actions'] == result['service_or_timer_actions'] == result['messages_sent'] == 0
    preserved(tree)


def test_apply_single_worker_and_private_complete_metadata_backup(tree):
    result = tool.update(tree.payload, tree.after, apply=True)
    assert result['passed'] and tree.worker.read_bytes() == tree.data
    saved = Path(result['backup'])
    assert (saved / 'health_delivery.py').read_bytes() == tree.old
    metadata = json.loads((saved / 'metadata.json').read_text())
    assert metadata['worker'] == tree.before
    assert metadata['collector_existing_read_only_guard'] == tree.collector_image
    assert metadata['collector_backup_body'] is False
    assert metadata['collector_existing_read_only_guard'] is not None
    current = tool.read(tree.worker)[1]
    assert all(current[key] == tree.before[key] for key in ('uid', 'gid', 'mode', 'xattrs'))
    assert current['mtime_ns'] != tree.before['mtime_ns']  # Fresh code timestamp.
    if os.name == 'posix':
        assert stat.S_IMODE(saved.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in saved.iterdir())
    preserved(tree)


@pytest.mark.parametrize('kind', ['worker', 'collector', 'payload_hash', 'payload_path', 'missing_collector'])
def test_fixed_before_collector_payload_guards_refuse_without_writes(tree, kind):
    path, checksum = tree.payload, tree.after
    if kind == 'worker': tree.worker.write_bytes(b'FOREIGN_WORKER=True\n')
    if kind == 'collector': tree.collector.write_bytes(b'FOREIGN_COLLECTOR=True\n')
    if kind == 'payload_hash': checksum = 'f' * 64
    if kind == 'payload_path':
        path = tree.payloads / 'unapproved.py'; path.write_bytes(tree.data)
    if kind == 'missing_collector': tree.collector.unlink()
    with pytest.raises((tool.Refused, FileNotFoundError)):
        tool.update(path, checksum, apply=True)
    assert not list(tree.backups.iterdir())


def test_memory_compile_failure_never_changes_source_or_collector(tree, monkeypatch):
    def reject(data): raise tool.Refused('task_python310_memory_compile_failed')
    monkeypatch.setattr(tool, 'compile_only', reject)
    with pytest.raises(tool.Refused, match='memory_compile'):
        tool.update(tree.payload, tree.after, apply=True)
    assert tree.worker.read_bytes() == tree.old and not list(tree.backups.iterdir())
    preserved(tree)


def test_failure_after_replace_restores_old_bytes_and_metadata(tree, monkeypatch):
    failed = False
    def fail_once(path):
        nonlocal failed
        if path == tree.app and not failed:
            failed = True
            raise OSError('synthetic fsync after replacement failure')
    monkeypatch.setattr(tool, 'sync', fail_once)
    result = tool.update(tree.payload, tree.after, apply=True)
    assert not result['passed'] and result['rollback'] == 'restored_before_bytes_and_restorable_metadata'
    restored = tool.read(tree.worker)[1]
    assert tree.worker.read_bytes() == tree.old
    assert all(restored[key] == tree.before[key] for key in ('uid', 'gid', 'mode', 'mtime_ns', 'xattrs'))
    assert not list(tree.app.glob('.health-delivery-ack-*'))
    preserved(tree)


def test_foreign_after_image_is_retained_on_rollback(tree, monkeypatch):
    foreign = b'EXTERNAL_WORKER_AFTER_IMAGE=True\n'
    real = tool.atomic
    def replace_then_change(data, previous, planned, **kwargs):
        real(data, previous, planned, **kwargs)
        tree.worker.write_bytes(foreign)
        raise OSError('synthetic failure after external change')
    monkeypatch.setattr(tool, 'atomic', replace_then_change)
    result = tool.update(tree.payload, tree.after, apply=True)
    assert not result['passed'] and result['rollback'] == 'retained_foreign_after_image'
    assert tree.worker.read_bytes() == foreign
    preserved(tree)


def test_concurrent_collector_change_rolls_back_only_owned_worker(tree, monkeypatch):
    foreign = b'EXTERNAL_COLLECTOR=True\n'
    real = tool.atomic
    def update_then_change(data, previous, planned, **kwargs):
        real(data, previous, planned, **kwargs)
        if data == tree.data: tree.collector.write_bytes(foreign)
    monkeypatch.setattr(tool, 'atomic', update_then_change)
    result = tool.update(tree.payload, tree.after, apply=True)
    assert not result['passed'] and not result['collector_guard_unchanged']
    assert result['rollback'] == 'restored_before_bytes_and_restorable_metadata'
    assert tree.worker.read_bytes() == tree.old and tree.collector.read_bytes() == foreign
    assert result['collector_written_or_removed'] is False


def test_foreign_before_image_during_staging_is_not_overwritten(tree, monkeypatch):
    foreign = b'EXTERNAL_BEFORE_REPLACE=True\n'
    real = tool.restore_attributes
    def change_while_staging(path, value):
        real(path, value)
        tree.worker.write_bytes(foreign)
    monkeypatch.setattr(tool, 'restore_attributes', change_while_staging)
    result = tool.update(tree.payload, tree.after, apply=True)
    assert not result['passed'] and result['code'] == 'worker_changed_before_atomic_replace'
    assert result['rollback'] == 'retained_foreign_after_image' and tree.worker.read_bytes() == foreign
    preserved(tree)


def test_real_compile_adapter_is_compile_only_without_import_or_exec(tree, monkeypatch):
    captured = []
    def fake(parts, **kwargs):
        captured.append((parts, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'{"compiled":true,"python":"3.10.19"}', stderr=b'')
    monkeypatch.setattr(tool.subprocess, 'run', fake)
    assert REAL_COMPILE(tree.data) == '3.10.19'
    args, call = captured[0]
    assert args[:3] == [str(tool.TASK_PYTHON), '-I', '-B'] and 'compile(' in args[-1]
    assert 'exec(' not in args[-1] and 'app.health' not in args[-1]
    assert call['env'] == {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'}
