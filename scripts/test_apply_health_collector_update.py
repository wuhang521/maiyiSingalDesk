"""Synthetic filesystem transactions; never imports private or product modules."""
from contextlib import nullcontext
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('health_update_under_test', Path(__file__).with_name('apply-health-collector-update.py'))
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    app, backups, archives = [tmp_path / name for name in ('app', 'backups', 'archives')]
    for path in (app, backups, archives):
        path.mkdir(mode=0o700)
    old = b'OLD_WORKER = True\n'
    worker = app / 'health_delivery.py'
    worker.write_bytes(old)
    worker.chmod(0o640)
    os.utime(worker, ns=(1670000000000000000, 1670000000000000000))
    for name, value in [('APP', app), ('BACKUPS', backups), ('ARCHIVES', archives), ('BEFORE', tool.digest(old))]:
        monkeypatch.setattr(tool, name, value)
    monkeypatch.setattr(tool, 'owner', lambda *args: None)
    monkeypatch.setattr(tool, 'sync_directory', lambda *args: None)
    monkeypatch.setattr(tool, 'locked_worker', lambda *args: nullcontext())
    monkeypatch.setattr(tool, 'compile_in_task', lambda payload: '3.10.11')
    payload = {tool.WORKER: b'NEW_WORKER = True\n', tool.COLLECTOR: b'NEW_COLLECTOR = True\n'}
    return SimpleNamespace(app=app, backups=backups, archives=archives, old=old,
                           before=tool.read_regular(worker)[1], payload=payload)


def package(tree, members=None):
    archive = tree.archives / 'signaldesk-health-collector-synthetic.tar.gz'
    entries = list(tree.payload.items()) if members is None else members
    with tarfile.open(archive, mode='w:gz', format=tarfile.USTAR_FORMAT) as output:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            if data is None:
                member.type = tarfile.SYMTYPE
                member.linkname = '/tmp/synthetic-target'
                output.addfile(member)
            else:
                member.size = len(data)
                output.addfile(member, io.BytesIO(data))
    return archive, tool.digest(archive.read_bytes()), {name: tool.digest(data) for name, data in tree.payload.items()}


def test_default_dry_run_compiles_without_writes(tree, monkeypatch):
    archive, checksum, expected = package(tree)
    compiled = []
    monkeypatch.setattr(tool, 'compile_in_task', lambda data: compiled.append(data) or '3.10.11')
    result = tool.update(archive, checksum, expected)
    assert result['passed'] and result['mode'] == 'dry_run'
    assert compiled == [tree.payload]
    assert (tree.app / 'health_delivery.py').read_bytes() == tree.old
    assert not (tree.app / 'health_collector.py').exists()
    assert not list(tree.backups.iterdir())


def test_apply_preserves_metadata_and_backups_and_installs_module_first(tree, monkeypatch):
    archive, checksum, expected = package(tree)
    real, order = tool.atomic, []
    def recording(path, data, metadata, planned, key):
        if path.parent == tree.app:
            order.append(key)
        return real(path, data, metadata, planned, key)
    monkeypatch.setattr(tool, 'atomic', recording)
    result = tool.update(archive, checksum, expected, apply=True)
    assert result['passed'] and order == [tool.COLLECTOR, tool.WORKER]
    for name, data in tree.payload.items():
        assert (tree.app / Path(name).name).read_bytes() == data
    installed = tool.read_regular(tree.app / 'health_delivery.py')[1]
    for field in ('uid', 'gid', 'mode', 'mtime_ns'):
        assert installed[field] == tree.before[field]
    backup = Path(result['backup'])
    assert (backup / 'health_delivery.py').read_bytes() == tree.old
    assert json.loads((backup / 'metadata.json').read_text())[tool.WORKER] == tree.before
    if os.name == 'posix':
        assert stat.S_IMODE(backup.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in backup.iterdir())
    assert result['service_actions'] == result['database_actions'] == result['messages_sent'] == 0


@pytest.mark.parametrize('kind', ['before', 'existing_collector', 'archive_sha', 'member_sha'])
def test_guards_reject_without_writes(tree, kind):
    archive, checksum, expected = package(tree)
    if kind == 'before':
        (tree.app / 'health_delivery.py').write_bytes(b'FOREIGN = True\n')
    elif kind == 'existing_collector':
        (tree.app / 'health_collector.py').write_bytes(b'EXISTING = True\n')
    elif kind == 'archive_sha':
        checksum = '0' * 64
    else:
        expected[tool.COLLECTOR] = '0' * 64
    before = {path.name: path.read_bytes() for path in tree.app.iterdir()}
    with pytest.raises(tool.Refused):
        tool.update(archive, checksum, expected, apply=True)
    assert {path.name: path.read_bytes() for path in tree.app.iterdir()} == before
    assert not list(tree.backups.iterdir())


@pytest.mark.parametrize('extra', ['../../outside.py', 'app/other.py', 'duplicate', 'missing', 'symlink'])
def test_only_two_regular_unique_archive_members(tree, extra):
    members = list(tree.payload.items())
    if extra == 'missing':
        members.pop()
    elif extra == 'duplicate':
        members.append(members[0])
    elif extra == 'symlink':
        members[1] = (tool.COLLECTOR, None)
    else:
        members.append((extra, b'UNRELATED = True\n'))
    archive, checksum, expected = package(tree, members)
    with pytest.raises(tool.Refused):
        tool.update(archive, checksum, expected, apply=True)
    assert (tree.app / 'health_delivery.py').read_bytes() == tree.old
    assert not list(tree.backups.iterdir())


def test_failed_runtime_compile_has_no_backup_or_replacement(tree, monkeypatch):
    archive, checksum, expected = package(tree)
    def reject(payload):
        raise tool.Refused('task_memory_compile_failed')
    monkeypatch.setattr(tool, 'compile_in_task', reject)
    with pytest.raises(tool.Refused, match='task_memory_compile_failed'):
        tool.update(archive, checksum, expected, apply=True)
    assert (tree.app / 'health_delivery.py').read_bytes() == tree.old
    assert not list(tree.backups.iterdir())


def test_failure_after_module_commit_restores_exact_original(tree, monkeypatch):
    archive, checksum, expected = package(tree)
    real = tool.atomic
    def interrupted(path, data, metadata, planned, key):
        if key == tool.WORKER:
            raise OSError('synthetic before worker replace')
        return real(path, data, metadata, planned, key)
    monkeypatch.setattr(tool, 'atomic', interrupted)
    result = tool.update(archive, checksum, expected, apply=True)
    assert not result['passed']
    assert result['rollback'][tool.COLLECTOR] == 'restored_before_image'
    assert tool.read_regular(tree.app / 'health_delivery.py')[1] == tree.before
    assert not (tree.app / 'health_collector.py').exists()


def test_failure_immediately_after_worker_replace_is_recoverable(tree, monkeypatch):
    archive, checksum, expected = package(tree)
    calls = []
    def fsync_failure(path):
        calls.append(path)
        # Backup has two file fsync-dir calls and one parent sync; module fourth,
        # worker fifth. Failure here occurs after os.replace, before return.
        if len(calls) == 5:
            raise OSError('synthetic directory sync failure')
    monkeypatch.setattr(tool, 'sync_directory', fsync_failure)
    result = tool.update(archive, checksum, expected, apply=True)
    assert not result['passed']
    assert result['rollback'][tool.WORKER] == 'restored_before_image'
    assert result['rollback'][tool.COLLECTOR] == 'restored_before_image'
    restored = tool.read_regular(tree.app / 'health_delivery.py')[1]
    for field in ('sha256', 'uid', 'gid', 'mode', 'mtime_ns'):
        assert restored[field] == tree.before[field]
    assert not (tree.app / 'health_collector.py').exists()


def test_rollback_retains_external_third_image(tree, monkeypatch):
    archive, checksum, expected = package(tree)
    real = tool.atomic
    def changed_after_commit(path, data, metadata, planned, key):
        result = real(path, data, metadata, planned, key)
        if key == tool.WORKER:
            path.write_bytes(b'THIRD_IMAGE = True\n')
            raise OSError('synthetic concurrent replacement')
        return result
    monkeypatch.setattr(tool, 'atomic', changed_after_commit)
    result = tool.update(archive, checksum, expected, apply=True)
    assert not result['passed'] and result['rollback'][tool.WORKER] == 'retained_changed_image'
    assert (tree.app / 'health_delivery.py').read_bytes() == b'THIRD_IMAGE = True\n'
    assert not (tree.app / 'health_collector.py').exists()


def test_parent_symlink_is_rejected_even_in_dry_run(tree, monkeypatch):
    archive, checksum, expected = package(tree)
    original = Path.lstat
    def injected(path):
        if path == tree.app:
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
        return original(path)
    monkeypatch.setattr(Path, 'lstat', injected)
    with pytest.raises(tool.Refused, match='symlink_rejected'):
        tool.update(archive, checksum, expected)
    assert not list(tree.backups.iterdir())


def test_memory_compile_invokes_only_fixed_task_runtime(tree, monkeypatch):
    captured = []
    def run(command, **kwargs):
        captured.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"python":"3.10.11","compiled":2}', stderr='')
    monkeypatch.setattr(tool.subprocess, 'run', run)
    assert tool.compile_in_task is not None
    # Fixture replaces the compiler; retrieve the original implementation from
    # a separate module instance to exercise its actual subprocess contract.
    fresh = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(fresh)
    assert fresh.compile_in_task(tree.payload) == '3.10.11'
    command, options = captured[0]
    assert command[:3] == [str(fresh.TASK_PYTHON), '-I', '-B']
    assert 'compile(' in command[-1] and 'import app' not in command[-1] and 'from src' not in command[-1]
    assert set(json.loads(options['input'])) == set(tool.MEMBERS)
    assert options['env'] == {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'}
    assert options['capture_output'] and options['timeout'] == 30
