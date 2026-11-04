"""Default dry-run; update only the fixed public health Worker after ACK review.

No DB/env/service/timer/messaging operations. The existing public collector is a
read-only exact-image guard, and is never written, removed, or marked absent.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid

APP = Path('/opt/signaldesk-task/app')
WORKER = APP / 'health_delivery.py'
COLLECTOR = APP / 'health_collector.py'
BACKUPS = Path('/var/backups/signaldesk-task')
PAYLOADS = Path('/var/tmp')
TASK_PYTHON = Path('/opt/signaldesk-task/.venv/bin/python')
BEFORE = 'aad239c7566837705a9ef8d9c9bccae90adfa0c3f27eb90fa315816a84bf586e'
COLLECTOR_SHA = '04e179b61df6d86e3c714c49fca297e9517fc01b8df674c083743d0fa87cf5be'
LIMIT = 2 * 1024 * 1024


class Refused(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe(path, *, absent=False):
    require(path.is_absolute(), 'absolute_path_required')
    for parent in reversed(path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'unsafe_parent')
    if not absent or path.exists() or path.is_symlink():
        require(not stat.S_ISLNK(path.lstat().st_mode), 'symlink_rejected')


def attributes(path):
    if not hasattr(os, 'listxattr'):
        return {}
    return {key: base64.b64encode(os.getxattr(path, key)).decode('ascii') for key in sorted(os.listxattr(path))}


def metadata(info, data, attrs):
    return {'sha256': sha(data), 'bytes': len(data), 'device': info.st_dev, 'inode': info.st_ino,
            'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
            'stat_mode': info.st_mode, 'nlink': info.st_nlink,
            'atime_ns': info.st_atime_ns, 'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns,
            'xattrs': attrs}


def read(path):
    safe(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_NOATIME', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= LIMIT,
                'regular_single_link_bounded_file_required')
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            data = stream.read(LIMIT + 1)
        require(len(data) == info.st_size, 'file_size_changed')
        result = metadata(info, data, attributes(descriptor if hasattr(os, 'listxattr') else path))
        current = path.lstat()
        require((current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) ==
                (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns), 'file_changed_during_read')
        return data, result
    finally:
        os.close(descriptor)


def comparable(image):
    # atime may change on platforms without O_NOATIME; ctime/inode are audit
    # information, not restorable timestamps. Inode stays in the CAS comparison.
    return {key: value for key, value in image.items() if key not in {'atime_ns', 'ctime_ns'}}


def same(path, image):
    try:
        return comparable(read(path)[1]) == comparable(image)
    except (OSError, Refused):
        return False


def owner(path, uid, gid):
    os.chown(path, uid, gid)


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def restore_attributes(path, value):
    if not hasattr(os, 'listxattr'):
        require(not value, 'xattr_not_supported')
        return
    for key in os.listxattr(path):
        if key not in value:
            os.removexattr(path, key)
    for key, encoded in value.items():
        os.setxattr(path, key, base64.b64decode(encoded, validate=True))


@contextmanager
def locked():
    import fcntl
    safe(WORKER)
    descriptor = os.open(WORKER, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                (WORKER.lstat().st_dev, WORKER.lstat().st_ino) == (info.st_dev, info.st_ino), 'worker_lock_changed')
        yield
    finally:
        os.close(descriptor)


def compile_only(data):
    code = ('import base64,json,sys;assert sys.version_info[:2]==(3,10);'
            'compile(base64.b64decode(sys.stdin.buffer.read()),"health_delivery.py","exec");'
            'print(json.dumps({"compiled":True,"python":sys.version.split()[0]}))')
    result = subprocess.run([str(TASK_PYTHON), '-I', '-B', '-c', code], input=base64.b64encode(data),
        capture_output=True, timeout=30, env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'})
    require(result.returncode == 0, 'task_python310_memory_compile_failed')
    reply = json.loads(result.stdout)
    require(reply.get('compiled') is True and re.fullmatch(r'3\.10\.\d+', reply.get('python', '')), 'task_compile_contract')
    return reply['python']


def backup(old, worker_image, collector_image):
    safe(BACKUPS)
    info = BACKUPS.stat()
    if os.name == 'posix':
        require(info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o022, 'backup_parent_not_root_controlled')
    directory = BACKUPS / ('health-ack-update-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12])
    directory.mkdir(mode=0o700)
    owner(directory, 0, 0)
    os.chmod(directory, 0o700)
    records = {'health_delivery.py': old, 'metadata.json': json.dumps({
        'worker': worker_image, 'collector_existing_read_only_guard': collector_image,
        'collector_backup_body': False,
        'restorable_worker_metadata': ['uid', 'gid', 'mode', 'atime_ns', 'mtime_ns', 'xattrs'],
        'inode_device_ctime_recorded_for_audit_not_restorable': True}, sort_keys=True).encode()}
    for name, value in records.items():
        descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        owner(directory / name, 0, 0)
        os.chmod(directory / name, 0o600)
    sync(directory)
    sync(BACKUPS)
    return directory


def atomic(data, previous, planned, *, expected_current, restore_times=False):
    safe(WORKER)
    descriptor, name = tempfile.mkstemp(prefix='.health-delivery-ack-', dir=APP)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            owner(temporary, previous['uid'], previous['gid'])
            os.chmod(temporary, previous['mode'])
            restore_attributes(temporary, previous['xattrs'])
            if restore_times:
                os.utime(temporary, ns=(previous['atime_ns'], previous['mtime_ns']))
            os.fsync(stream.fileno())
        planned.update(read(temporary)[1])
        require(same(WORKER, expected_current), 'worker_changed_before_atomic_replace')
        os.replace(temporary, WORKER)
        sync(APP)
        require(same(WORKER, planned), 'installed_after_image_changed')
    finally:
        if temporary.exists():
            temporary.unlink()


def public_image(image):
    # Extended attribute values remain only in the server-private backup.
    return {key: image[key] for key in ('sha256', 'bytes', 'uid', 'gid', 'mode', 'device', 'inode', 'mtime_ns')}


def update(payload, after_sha, *, apply=False):
    require(payload.parent == PAYLOADS and re.fullmatch(r'signaldesk-health-ack-[a-z0-9-]{1,96}\.py', payload.name), 'payload_path_rejected')
    require(re.fullmatch(r'[0-9a-f]{64}', after_sha or '') is not None and after_sha != BEFORE, 'after_sha_required')
    data, payload_image = read(payload)
    require(payload_image['sha256'] == after_sha, 'payload_after_sha_mismatch')
    with locked() if apply else nullcontext():
        old, before = read(WORKER)
        require(before['sha256'] == BEFORE, 'worker_before_sha_mismatch')
        _, collector = read(COLLECTOR)
        require(collector['sha256'] == COLLECTOR_SHA, 'collector_guard_sha_mismatch')
        python = compile_only(data)
        require(same(WORKER, before) and same(COLLECTOR, collector) and same(payload, payload_image), 'input_changed_before_update')
        result = {'passed': True, 'mode': 'apply' if apply else 'dry_run', 'task_python': python,
                  'before_sha256': BEFORE, 'after_sha256': after_sha, 'collector_sha256': COLLECTOR_SHA,
                  'collector_written_or_removed': False, 'database_actions': 0, 'env_actions': 0,
                  'service_or_timer_actions': 0, 'messages_sent': 0}
        if not apply:
            return result
        directory = backup(old, before, collector)
        result['backup'] = str(directory)
        planned = {}
        try:
            require(same(WORKER, before) and same(COLLECTOR, collector), 'input_changed_after_backup')
            atomic(data, before, planned, expected_current=before)
            require(same(WORKER, planned), 'final_worker_after_image_changed')
            require(same(COLLECTOR, collector), 'collector_changed_externally')
            result['installed'] = public_image(planned)
            result['collector_guard_unchanged'] = True
        except BaseException as error:
            result.update(passed=False, error_type=type(error).__name__,
                          code=str(error) if isinstance(error, Refused) else 'worker_update_failed')
            if same(WORKER, before):
                result['rollback'] = 'already_before_image'
            elif not planned or not same(WORKER, planned):
                result['rollback'] = 'retained_foreign_after_image'
            else:
                try:
                    restored = {}
                    atomic(old, before, restored, expected_current=planned, restore_times=True)
                    require(same(WORKER, restored) and restored['sha256'] == BEFORE, 'rollback_not_verified')
                    result['rollback'] = 'restored_before_bytes_and_restorable_metadata'
                except Exception as restore_error:
                    result['rollback'] = 'rollback_error_' + type(restore_error).__name__
            result['collector_guard_unchanged'] = same(COLLECTOR, collector)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--after-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        require(os.name == 'posix', 'posix_managed_host_required')
        require(not args.apply or os.geteuid() == 0, 'apply_requires_root')
        result = update(args.payload, args.after_sha256, apply=args.apply)
    except Exception as error:
        result = {'passed': False, 'error_type': type(error).__name__,
                  'code': str(error) if isinstance(error, Refused) else 'preflight_failed',
                  'collector_written_or_removed': False, 'database_actions': 0,
                  'env_actions': 0, 'service_or_timer_actions': 0, 'messages_sent': 0}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
