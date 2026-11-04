"""Reviewable two-file update. Dry run is the default; no services or DB actions.

The CLI has no destination/before-image override. Tests replace module constants
inside their own temporary tree; production always uses the fixed paths below.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
import uuid

APP = Path('/opt/signaldesk-task/app')
BACKUPS = Path('/var/backups/signaldesk-task')
ARCHIVES = Path('/var/tmp')
TASK_PYTHON = Path('/opt/signaldesk-task/.venv/bin/python')
BEFORE = '9c7a8995c26456aef4cab1d24b9e5f9bed490a1735279a4a9fffc0f8bb20138d'
WORKER = 'app/health_delivery.py'
COLLECTOR = 'app/health_collector.py'
MEMBERS = (WORKER, COLLECTOR)
INSTALL_ORDER = (COLLECTOR, WORKER)
LIMIT = 2 * 1024 * 1024


class Refused(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Refused(code)


def safe_parents(path: Path, *, leaf_exists: bool = True) -> None:
    """Reject symlinks, including every existing ancestor; never resolve them."""
    require(path.is_absolute(), 'absolute_path_required')
    for parent in reversed(path.parents):
        metadata = parent.lstat()
        require(stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode), 'unsafe_parent')
    if leaf_exists or path.exists() or path.is_symlink():
        require(not stat.S_ISLNK(path.lstat().st_mode), 'symlink_rejected')


def read_regular(path: Path, limit: int = LIMIT) -> tuple[bytes, dict]:
    safe_parents(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        metadata = os.fstat(descriptor)
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1, 'regular_single_link_required')
        require(metadata.st_size <= limit, 'file_too_large')
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            data = stream.read(limit + 1)
        require(len(data) <= limit, 'file_too_large')
        identity = image(metadata, data)
        require(path.lstat().st_ino == metadata.st_ino and path.lstat().st_dev == metadata.st_dev, 'file_identity_changed')
        return data, identity
    finally:
        os.close(descriptor)


def image(metadata: os.stat_result, data: bytes) -> dict:
    return {'sha256': digest(data), 'uid': metadata.st_uid, 'gid': metadata.st_gid,
            'mode': stat.S_IMODE(metadata.st_mode), 'mtime_ns': metadata.st_mtime_ns,
            'device': metadata.st_dev, 'inode': metadata.st_ino, 'bytes': len(data)}


def same_image(path: Path, expected: dict | None) -> bool:
    if expected is None:
        return not path.exists() and not path.is_symlink()
    try:
        return read_regular(path)[1] == expected
    except (OSError, Refused):
        return False


def archive_payload(archive: Path, archive_sha: str, expected: dict[str, str]) -> dict[str, bytes]:
    require(archive.parent == ARCHIVES and archive.name.startswith('signaldesk-health-collector-')
            and archive.name.endswith(('.tar.gz', '.tgz')), 'archive_path_rejected')
    require(set(expected) == set(MEMBERS), 'manifest_members_rejected')
    require(all(re.fullmatch('[0-9a-f]{64}', value) for value in [archive_sha, *expected.values()]), 'invalid_sha256')
    raw, _ = read_regular(archive, 4 * LIMIT)
    require(digest(raw) == archive_sha, 'archive_sha256_mismatch')
    payload = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as package:
        for member in package:
            require(member.isfile() and member.name in MEMBERS and member.name not in payload,
                    'unexpected_archive_member')
            require(0 < member.size <= LIMIT and not member.pax_headers, 'invalid_member_metadata')
            stream = package.extractfile(member)
            require(stream is not None, 'missing_member_data')
            data = stream.read(LIMIT + 1)
            require(len(data) == member.size and digest(data) == expected[member.name], 'member_sha256_mismatch')
            payload[member.name] = data
    require(set(payload) == set(MEMBERS), 'missing_archive_member')
    return payload


def compile_in_task(payload: dict[str, bytes]) -> str:
    # Compile only: no import of app/private source, bytecode cache, or execution.
    probe = ('import base64,json,sys;'
             'assert sys.version_info[:2]==(3,10),"Task Python 3.10 required";'
             'p=json.load(sys.stdin);'
             '[compile(base64.b64decode(v),k,"exec") for k,v in p.items()];'
             'print(json.dumps({"python":sys.version.split()[0],"compiled":len(p)}))')
    response = subprocess.run([str(TASK_PYTHON), '-I', '-B', '-c', probe],
        input=json.dumps({name: base64.b64encode(data).decode('ascii') for name, data in payload.items()}),
        capture_output=True, text=True, timeout=30,
        env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'})
    require(response.returncode == 0, 'task_memory_compile_failed')
    result = json.loads(response.stdout)
    require(result['compiled'] == 2 and re.fullmatch(r'3\.10\.\d+', result['python']), 'task_compile_contract_failed')
    return result['python']


def owner(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def locked_worker(path: Path):
    import fcntl
    safe_parents(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = os.fstat(descriptor)
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1, 'unsafe_locked_worker')
        require(path.lstat().st_ino == metadata.st_ino and path.lstat().st_dev == metadata.st_dev,
                'locked_worker_changed')
        yield
    finally:
        os.close(descriptor)


def atomic(path: Path, data: bytes, metadata: dict, planned: dict, key: str) -> None:
    safe_parents(path, leaf_exists=False)
    descriptor, temporary_name = tempfile.mkstemp(prefix='.' + path.name + '.collector-', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            owner(temporary, metadata['uid'], metadata['gid'])
            os.chmod(temporary, metadata['mode'])
            if 'mtime_ns' in metadata:
                os.utime(temporary, ns=(metadata['mtime_ns'], metadata['mtime_ns']))
            os.fsync(stream.fileno())
            # Record the staged inode before replace: even failure immediately
            # after replace is recoverable, without guessing an after-image.
            planned[key] = image(os.fstat(stream.fileno()), data)
        os.replace(temporary, path)
        sync_directory(path.parent)
        require(same_image(path, planned[key]), 'installed_image_changed')
    finally:
        if temporary.exists():
            temporary.unlink()


def private_backup(data: bytes, metadata: dict) -> Path:
    safe_parents(BACKUPS)
    directory = BACKUPS.lstat()
    require(stat.S_ISDIR(directory.st_mode), 'backup_parent_not_directory')
    if os.name == 'posix':
        require(directory.st_uid == 0 and not stat.S_IMODE(directory.st_mode) & 0o022,
                'backup_parent_not_root_controlled')
    backup = BACKUPS / ('health-collector-update-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                        + '-' + uuid.uuid4().hex[:12])
    backup.mkdir(mode=0o700)
    owner(backup, 0, 0)
    os.chmod(backup, 0o700)
    for name, value in [('health_delivery.py', data),
                        ('metadata.json', json.dumps({WORKER: metadata, COLLECTOR: None}, sort_keys=True).encode())]:
        planned = {}
        atomic(backup / name, value, {'uid': 0, 'gid': 0, 'mode': 0o600}, planned, name)
    sync_directory(BACKUPS)
    return backup


def update(archive: Path, archive_sha: str, expected: dict[str, str], *, apply: bool = False) -> dict:
    # Lock the original worker inode without creating a third product file.
    with locked_worker(APP / 'health_delivery.py') if apply else nullcontext():
        return guarded_update(archive, archive_sha, expected, apply=apply)


def guarded_update(archive: Path, archive_sha: str, expected: dict[str, str], *, apply: bool = False) -> dict:
    payload = archive_payload(archive, archive_sha, expected)
    safe_parents(APP)
    require(stat.S_ISDIR(APP.lstat().st_mode), 'app_not_directory')
    targets = {name: APP / Path(name).name for name in MEMBERS}
    old, before = read_regular(targets[WORKER])
    require(before['sha256'] == BEFORE, 'worker_before_sha256_mismatch')
    require(same_image(targets[COLLECTOR], None), 'collector_must_be_absent')
    python = compile_in_task(payload)
    result = {'passed': True, 'mode': 'apply' if apply else 'dry_run', 'task_python': python,
              'before_worker_sha256': BEFORE, 'after': expected, 'archive_sha256': archive_sha,
              'database_actions': 0, 'service_actions': 0, 'messages_sent': 0}
    if not apply:
        return result
    backup = private_backup(old, before)
    result['backup'] = str(backup)
    planned = {}
    prior = {WORKER: before, COLLECTOR: None}
    try:
        # The module must exist before the new worker can import it. The old
        # worker safely ignores the new module during this short interval.
        for name in INSTALL_ORDER:
            # Re-check the complete original image immediately before each write.
            require(same_image(targets[name], prior[name]), 'target_changed_before_write')
            metadata = before if name == WORKER else {key: before[key] for key in ('uid', 'gid', 'mode')}
            atomic(targets[name], payload[name], metadata, planned, name)
        require(all(same_image(targets[name], planned[name]) for name in MEMBERS), 'final_image_changed')
        result['installed'] = planned
    except BaseException as error:
        result.update(passed=False, error_type=type(error).__name__)
        rollback = {}
        for name in reversed(INSTALL_ORDER):
            path = targets[name]
            if same_image(path, prior[name]):
                rollback[name] = 'already_before_image'
            elif name not in planned or not same_image(path, planned[name]):
                rollback[name] = 'retained_changed_image'
            else:
                try:
                    if name == COLLECTOR:
                        path.unlink()
                        sync_directory(path.parent)
                    else:
                        atomic(path, old, before, {}, 'rollback')
                    rollback[name] = 'restored_before_image'
                except Exception as rollback_error:
                    rollback[name] = 'rollback_error_' + type(rollback_error).__name__
        result['rollback'] = rollback
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--worker-sha256', required=True)
    parser.add_argument('--collector-sha256', required=True)
    parser.add_argument('--apply', action='store_true', help='Write the reviewed two-file update; default is dry run.')
    args = parser.parse_args(argv)
    try:
        require(os.name == 'posix', 'posix_host_required')
        require(not args.apply or os.geteuid() == 0, 'apply_requires_root')
        result = update(args.archive, args.archive_sha256,
                        {WORKER: args.worker_sha256, COLLECTOR: args.collector_sha256}, apply=args.apply)
    except Exception as error:
        result = {'passed': False, 'error_type': type(error).__name__,
                  'code': str(error) if isinstance(error, Refused) else 'preflight_failed',
                  'database_actions': 0, 'service_actions': 0, 'messages_sent': 0}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
