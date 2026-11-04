"""Plan (default) or activate the reviewed health-only schedule on its fixed host.

No migration, article write, private source export, UI claim, or env/topic rewrite.
The two after-image SHAs and both acceptance evidence SHAs are supplied by the
reviewer. Synthetic tests replace constants/adapters, never CLI destination paths.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import uuid
from zoneinfo import ZoneInfo

TASK = Path('/opt/signaldesk-task')
COLLECTOR = Path('/opt/signaldesk')
SYSTEM = Path('/etc/systemd/system')
BACKUPS = Path('/var/backups/signaldesk-task')
ACCEPTANCE = Path('/var/lib')
STATE = Path('/var/lib/signaldesk-health')
PRIVATE_CONFIG = Path('/etc/signaldesk/health-delivery.env')
DROPIN = SYSTEM / 'signaldesk-worker.service.d/health-managed.conf'
DROPIN_BYTES = b'[Service]\nEnvironment=SIGNALDESK_HEALTH_MANAGED=1\n'
GUARD_SHA = 'b704ec9cced3c0da0519591ffb400b3e27c10721f79a18789029b27ed87e3697'
SOURCE_KEYS = ('app/health_delivery.py', 'app/health_collector.py')
UNIT_SHA = {
    'signaldesk-health-prepare.service': '2c04534ab9a922f211028f2414f89d4e939adcf84fa1b169f8477f0a2d26a98c',
    'signaldesk-health-prepare.timer': '7ef467afd2005476e0cf40e4c33c69357fa7621eec08eec3b9193a6fbbc47cc6',
    'signaldesk-health-deliver.service': 'c61e902254ae9a86fc2d4a3e2e0a64c86f6cd89c0cce5b59eab9236115bc97b3',
    'signaldesk-health-deliver.timer': 'd6e5ada801790387135d3df8a4270cfcbfd8c7d5549148dc8a1d2d7be872fb9e',
}
LEGACY = 'signaldesk-worker.service'
TIMERS = ('signaldesk-health-prepare.timer', 'signaldesk-health-deliver.timer')
SERVICES = ('signaldesk-health-prepare.service', 'signaldesk-health-deliver.service')
SHANGHAI = ZoneInfo('Asia/Shanghai')
START_DATE = '2026-10-31'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
       'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'}


class Refused(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def safe_path(path, *, absent=False):
    require(path.is_absolute(), 'absolute_path_required')
    for parent in reversed(path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'unsafe_parent')
    if not absent or path.exists() or path.is_symlink():
        require(not stat.S_ISLNK(path.lstat().st_mode), 'symlink_rejected')


def read_file(path, limit=2 * 1024 * 1024):
    safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
                'unsafe_regular_file')
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            data = stream.read(limit + 1)
        require(len(data) <= limit, 'file_limit_exceeded')
        require((path.lstat().st_dev, path.lstat().st_ino) == (info.st_dev, info.st_ino), 'file_changed')
        image = {'sha256': sha(data), 'uid': info.st_uid, 'gid': info.st_gid,
                 'mode': stat.S_IMODE(info.st_mode), 'mtime_ns': info.st_mtime_ns,
                 'device': info.st_dev, 'inode': info.st_ino}
        return data, image
    finally:
        os.close(descriptor)


def image_matches(path, image):
    if image is None:
        return not path.exists() and not path.is_symlink()
    try:
        return read_file(path)[1] == image
    except (OSError, Refused):
        return False


def owner(path, uid, gid):
    os.chown(path, uid, gid)


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    owner(path, 0, 0)
    os.chmod(path, 0o600)


@contextmanager
def exclusive_system():
    # Lock an existing directory, without creating a global lock/config file.
    import fcntl
    safe_path(SYSTEM)
    descriptor = os.open(SYSTEM, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def command(parts, *, cwd=None, allow_failure=False):
    response = subprocess.run(parts, cwd=cwd, env=ENV, capture_output=True, text=True, timeout=90)
    if not allow_failure:
        require(response.returncode == 0, 'command_failed')
    # Never forward subprocess stderr, URLs, credentials, or source text.
    require(len(response.stdout) <= 2 * 1024 * 1024, 'command_output_limit')
    return response


class System:
    def state(self, unit):
        enabled = command(['systemctl', 'is-enabled', unit], allow_failure=True).stdout.strip()
        text = command(['systemctl', 'show', unit, '--property=LoadState', '--property=ActiveState']).stdout
        properties = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        # v219 is-active can say "unknown" for an inactive, garbage-collected
        # unit. Require the manager's explicit loaded/active properties instead.
        require(properties.get('LoadState') == 'loaded', 'unit_not_loaded')
        active = properties.get('ActiveState')
        require(enabled in {'enabled', 'disabled', 'static'}, 'unsupported_enabled_state')
        require(active in {'active', 'inactive', 'failed'}, 'unsupported_active_state')
        return {'enabled': enabled, 'active': active == 'active'}

    def action(self, verb, unit):
        require(unit in (LEGACY, *TIMERS, *SERVICES), 'unexpected_unit')
        require(verb in {'restart', 'start', 'stop', 'enable', 'disable'}, 'unexpected_action')
        command(['systemctl', verb, unit])  # v219: deliberately never --now.

    def reload(self):
        command(['systemctl', 'daemon-reload'])

    def guard_directive(self):
        text = command(['systemctl', 'show', LEGACY, '--property=Environment']).stdout
        values = shlex.split(text.removeprefix('Environment=').strip())
        return [v for v in values if v.startswith('SIGNALDESK_HEALTH_MANAGED=')] == ['SIGNALDESK_HEALTH_MANAGED=1']

    def calendar(self, timer, expected_time, now):
        require(timer in TIMERS, 'unexpected_calendar_timer')
        path_reply = command(['busctl', 'call', 'org.freedesktop.systemd1', '/org/freedesktop/systemd1',
                              'org.freedesktop.systemd1.Manager', 'GetUnit', 's', timer]).stdout
        path = re.fullmatch(r'o "(/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+)"\s*', path_reply)
        require(path is not None, 'typed_calendar_unit_path_required')
        text = command(['busctl', 'get-property', 'org.freedesktop.systemd1', path[1],
                        'org.freedesktop.systemd1.Timer', 'TimersCalendar']).stdout
        # v219 systemctl prints a(sst) as [unprintable]. Its D-Bus tuple gives
        # this calendar's realtime deadline, independently of the 2min retry.
        fields = shlex.split(text)
        expression = '*-*-* ' + expected_time
        require(len(fields) == 5 and fields[:3] == ['a(sst)', '1', 'OnCalendar'] and
                fields[3] == expression and re.fullmatch(r'[0-9]{1,20}', fields[4]) is not None,
                'actual_calendar_not_verified')
        timestamp = int(fields[4])
        require(0 < timestamp < 2**64-1, 'calendar_not_armed')
        seconds, microseconds = divmod(timestamp, 1000000)
        next_time = datetime.fromtimestamp(seconds, SHANGHAI) + timedelta(microseconds=microseconds)
        hour, minute, second = map(int, expected_time.split(':'))
        expected = now.astimezone(SHANGHAI).replace(hour=hour, minute=minute, second=second, microsecond=0)
        if expected <= now:
            expected += timedelta(days=1)
        require(next_time == expected and next_time > now, 'calendar_next_not_expected_daily')
        return {'on_calendar': '*-*-* ' + expected_time, 'next_calendar_at': next_time.isoformat(),
                'next_calendar_realtime_usec': timestamp,
                'source_property': 'D-Bus Timer.TimersCalendar.next_elapse_realtime_usec'}


def manager_time():
    response = command(['timedatectl', 'status'])
    zones = re.findall(r'^\s*Time zone:\s+([A-Za-z0-9_+/-]+)(?:\s|$)', response.stdout, re.M)
    ntp = re.findall(r'^\s*NTP synchronized:\s+(yes|no)(?:\s|$)', response.stdout, re.M)
    require(zones == ['Asia/Shanghai'] and ntp == ['yes'], 'timezone_or_ntp_gate')


TASK_PROBE = r'''
import hashlib,json
from pathlib import Path
import httpx
from dotenv import dotenv_values
from app.config import get_settings
from app.health_delivery import Config
def fp(server,topic):return hashlib.sha256((server.rstrip('/')+'\0'+topic.strip()).encode()).hexdigest()
c=Config.load(Path('/etc/signaldesk/health-delivery.env'))
v=dotenv_values(Path('/opt/signaldesk/.env'),interpolate=False).get('SIGNALDESK_HEALTH_MANAGED')
assert v in (None,'1')  # An explicit empty EnvironmentFile value also overrides 1.
with httpx.Client(base_url='http://127.0.0.1:19080',headers={'X-SignalDesk-Key':get_settings().api_key},timeout=20,trust_env=False) as client:
 r=client.get('/api/v1/channels');r.raise_for_status();rows=r.json()
h=[x for x in rows if x.get('id')=='health']
assert len(h)==1 and not any(x.get('id')=='demo-health' for x in rows)
h=h[0]
assert h.get('managed') is True and h.get('readViaApi') is True and h.get('state')=='active'
assert h.get('schedule')=='09:00' and h.get('maxItems')==100 and h.get('aliases')==['demo-health']
assert c.max_items==100 and c.start_date and c.start_date.isoformat()=='2026-10-31'
assert fp(c.server,c.topic)==fp(h['server'],h['topic'])
assert sum(fp(x.get('server',''),x.get('topic',''))==fp(c.server,c.topic) for x in rows)==1
print(json.dumps({'canonical':'health','aliases':['demo-health'],'schedule':'09:00','max_items':100,'start_date':c.start_date.isoformat(),'destination_sha256':fp(c.server,c.topic),'legacy_env_guard_conflict':False}))
'''
COLLECTOR_PROBE = r'''
import hashlib,json
from pathlib import Path
from src.main import load_config
p=Path('/opt/signaldesk/.env');c=load_config(p);h=[r for r in c.recipients if r.key=='health'];assert len(h)==1
print(json.dumps({'destination_sha256':hashlib.sha256((c.ntfy_server.rstrip('/')+'\0'+h[0].topic.strip()).encode()).hexdigest()}))
'''


def configuration():
    task = command([str(TASK / '.venv/bin/python'), '-B', '-c', TASK_PROBE], cwd=TASK)
    collector = command([str(COLLECTOR / '.venv/bin/python'), '-B', '-c', COLLECTOR_PROBE], cwd=COLLECTOR)
    current, producer = json.loads(task.stdout), json.loads(collector.stdout)
    require(valid_sha(current.get('destination_sha256')) and
            current['destination_sha256'] == producer.get('destination_sha256') and
            current.get('legacy_env_guard_conflict') is False, 'producer_destination_or_guard_mismatch')
    return current


def checked_json(path, expected):
    require(valid_sha(expected), 'invalid_evidence_sha256')
    data, metadata = read_file(path)
    require(sha(data) == expected, 'evidence_sha256_mismatch')
    return json.loads(data), metadata


def timer_evidence(path, expected, after, now):
    document, metadata = checked_json(path, expected)
    stage = Path(document.get('state_dir', ''))
    require(stage.parent == ACCEPTANCE and re.fullmatch(r'signaldesk-health-timer-acceptance-[a-z0-9-]{8,80}', stage.name), 'timer_stage_rejected')
    require(path.parent == stage, 'timer_evidence_outside_stage')
    safe_path(stage)
    if os.name == 'posix':
        info = stage.stat()
        require(info.st_uid in {0, 994} and stat.S_IMODE(info.st_mode) == 0o700 and
                metadata['uid'] in {0, 994} and metadata['mode'] == 0o600, 'timer_evidence_not_private')
    require(document.get('schema') == 'signaldesk.health-timer-acceptance.v1' and
            document.get('passed') is True and document.get('phase') == 'verified' and
            document.get('test_run') is True and document.get('clock_override') is False and
            document.get('manager_timezone') == 'Asia/Shanghai' and document.get('ntp_synchronized') is True,
            'timer_evidence_not_real_verified')
    require(document.get('synthetic_only') is not True, 'synthetic_timer_evidence_rejected')
    scheduled = datetime.fromisoformat(document['scheduled_at'])
    require(scheduled.tzinfo is not None and scheduled <= now, 'timer_calendar_not_past')
    trigger = document.get('timer', {}).get('last_trigger_monotonic_usec', 0)
    first, second = document.get('first_delivery', {}), document.get('second_delivery', {})
    require(isinstance(trigger, int) and trigger > 0 and first.get('monotonic_usec', 0) >= trigger,
            'timer_trigger_missing')
    require(document.get('service', {}).get('result') == 'success' and
            document.get('service', {}).get('exec_main_status') == 0, 'timer_service_not_successful')
    for phase in (first, second):
        require(phase.get('state') == 'sent' and
                all(phase.get(key) == 1 for key in ('articles', 'batch_rows', 'ack_count', 'attempts_total')) and
                valid_sha(phase.get('ack_sha256')), 'timer_ledger_not_one_ack')
    require(first.get('ingest_calls') == 1 and first.get('publish_calls') == 1 and
            second.get('ingest_calls') == 0 and second.get('publish_calls') == 0 and
            first['ack_sha256'] == second['ack_sha256'] and document.get('dedupe_verified') is True,
            'timer_second_delivery_not_deduplicated')
    require(second.get('monotonic_usec', 0) >= first.get('monotonic_usec', 0), 'timer_second_not_after_first')
    require(document.get('source_sha256') == after, 'timer_source_not_current')
    artifacts = document.get('artifact_sha256', {})
    require(set(artifacts) == {'runner', 'unit', 'timer'} and all(valid_sha(x) for x in artifacts.values()),
            'timer_artifact_bindings_missing')
    return {'sha256': expected, 'state_dir': str(stage), 'verified_real_timer': True,
            'test_run_only': True, 'one_item_one_ack': True, 'dedupe_verified': True}


def normal_ledger():
    # Hash only these fixed private worker-ledger files. No SQL/business DB read.
    result = {}
    for name in ('ledger.sqlite3', 'ledger.sqlite3-wal', 'ledger.sqlite3-shm'):
        path = STATE / name
        if path.exists() or path.is_symlink():
            result[name] = read_file(path, 64 * 1024 * 1024)[1]['sha256']
    return result


def preflight(after, system, now):
    require(set(after) == set(SOURCE_KEYS) and all(valid_sha(x) for x in after.values()), 'after_manifest_rejected')
    identities = {}
    for key, expected in {**after, **{'unit:' + k: v for k, v in UNIT_SHA.items()}, 'private:guard': GUARD_SHA}.items():
        path = COLLECTOR / 'src/main.py' if key == 'private:guard' else SYSTEM / key[5:] if key.startswith('unit:') else TASK / key
        _, identity = read_file(path)
        require(identity['sha256'] == expected, 'deployed_source_sha256_mismatch')
        if os.name == 'posix':
            require(not identity['mode'] & 0o022 and identity['uid'] in {0, 994, 1001}, 'deployed_source_permissions')
            if key.startswith('unit:'):
                require((identity['uid'], identity['gid'], identity['mode']) == (0, 0, 0o644), 'unit_permissions')
        identities[key] = (path, identity)
    _, config_image = read_file(PRIVATE_CONFIG)
    manager_time()
    cfg = configuration()
    require(image_matches(PRIVATE_CONFIG, config_image), 'private_config_changed_during_probe')
    identities['private:config'] = (PRIVATE_CONFIG, config_image)
    require(cfg.get('start_date') == START_DATE and cfg.get('schedule') == '09:00' and cfg.get('max_items') == 100,
            'configuration_contract_mismatch')
    safe_path(DROPIN, absent=True) if DROPIN.parent.exists() else safe_path(DROPIN.parent, absent=True)
    before = None
    if DROPIN.exists():
        data, before = read_file(DROPIN)
        require(data == DROPIN_BYTES, 'existing_dropin_conflict')
        if os.name == 'posix':
            require((before['uid'], before['gid'], before['mode']) == (0, 0, 0o644), 'dropin_permissions')
    states = {unit: system.state(unit) for unit in (LEGACY, *TIMERS, *SERVICES)}
    require(states[LEGACY]['active'] and all(not states[unit]['active'] for unit in SERVICES), 'legacy_or_health_job_state_conflict')
    require(all(states[t]['enabled'] in {'enabled', 'disabled'} for t in TIMERS), 'timer_enable_state_conflict')
    return cfg, identities, before, states


def backup(before, states):
    safe_path(BACKUPS)
    info = BACKUPS.stat()
    if os.name == 'posix':
        require(info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o022, 'backup_parent_not_root_controlled')
    path = BACKUPS / ('health-schedule-activation-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12])
    path.mkdir(mode=0o700)
    owner(path, 0, 0)
    os.chmod(path, 0o700)
    private_json(path / 'before.json', {'dropin': before, 'units': states})
    if before is not None:
        private_json(path / 'old-dropin.json', {'content': DROPIN_BYTES.decode(), 'metadata': before})
    sync_dir(path)
    return path


def create_dropin(planned):
    require(image_matches(DROPIN, None), 'dropin_created_concurrently')
    descriptor, temporary = tempfile.mkstemp(prefix='.health-managed-', dir=DROPIN.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(DROPIN_BYTES)
            stream.flush()
            owner(temporary, 0, 0)
            os.chmod(temporary, 0o644)
            os.fsync(stream.fileno())
        planned.update(read_file(temporary)[1])
        # Atomic absent-path creation: an unexpected existing file is not replaced.
        os.link(temporary, DROPIN, follow_symlinks=False)
        temporary.unlink()
        sync_dir(DROPIN.parent)
        require(image_matches(DROPIN, planned), 'installed_dropin_changed')
    finally:
        if temporary.exists():
            temporary.unlink()


def activate(after, *, system=None, now=None, timer=None, timer_sha='', client=None, client_sha='', client_ui_reviewed=False, apply=False):
    system = system or System()
    now = (now or datetime.now(timezone.utc)).astimezone(SHANGHAI)
    with exclusive_system() if apply else nullcontext():
        cfg, identities, before, states = preflight(after, system, now)
        missing, accepted = [], {}
        if timer is None:
            missing.append('real_timer_acceptance')
        else:
            accepted['timer'] = timer_evidence(timer, timer_sha, after, now)
        if client is None or not client_ui_reviewed:
            missing.append('reviewed_client_receipt')
        if client is not None:
            require(client.parent == Path('/var/tmp') or (timer is not None and client.parent == timer.parent), 'client_receipt_path_rejected')
            raw, _ = read_file(client, 8 * 1024 * 1024)
            require(valid_sha(client_sha) and sha(raw) == client_sha, 'client_receipt_sha256_mismatch')
            accepted['client_receipt_sha256'] = client_sha
        result = {'passed': True, 'mode': 'activate' if apply else 'plan', 'activation_ready': not missing,
                  'missing_evidence': missing, 'server_timezone': 'Asia/Shanghai', 'ntp_synchronized': True,
                  'start_date': START_DATE, 'server_date': now.date().isoformat(),
                  'normal_delivery_due_today': now.date().isoformat() >= START_DATE,
                  'configuration': cfg, 'before_units': states, 'evidence': accepted,
                  'client_ui_review_asserted_by_operator': bool(client_ui_reviewed),
                  'client_ui_automatically_verified': False, 'direct_messages_sent': 0,
                  'migrations': 0, 'task_env_or_topic_writes': 0, 'service_actions': 0}
        if not apply:
            return result
        require(not missing, 'acceptance_evidence_required_before_activation')
        # Re-read all immutable code/config inputs before any activation write.
        require(all(image_matches(path, image) for path, image in identities.values()), 'source_changed_before_activation')
        require(image_matches(DROPIN, before), 'dropin_changed_before_activation')
        require({u: system.state(u) for u in states} == states, 'service_state_changed_before_activation')
        if before is not None and all(states[u] == {'enabled': 'enabled', 'active': True} for u in TIMERS) and system.guard_directive():
            result.update(activated=True, already_active=True)
            result['calendars'] = {u: system.calendar(u, '08:15:00' if 'prepare' in u else '09:00:00', now) for u in TIMERS}
            result['after_units'] = {u: system.state(u) for u in (LEGACY, *TIMERS)}
            return result  # No repeated worker restart or backup on a verified rerun.
        target_backup = backup(before, states)
        result['backup'] = str(target_backup)
        ledger_before = normal_ledger()
        planned, parent_created, expected = {}, None, {u: dict(s) for u, s in states.items()}
        owned_states = {u: [dict(s)] for u, s in states.items()}
        timer_started, changed = False, False
        try:
            if not DROPIN.parent.exists():
                DROPIN.parent.mkdir(mode=0o755)
                info = DROPIN.parent.stat()
                parent_created = (info.st_dev, info.st_ino)
                owner(DROPIN.parent, 0, 0)
                os.chmod(DROPIN.parent, 0o755)
            else:
                info = DROPIN.parent.stat()
                if os.name == 'posix':
                    require(info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o022, 'dropin_parent_not_root_controlled')
            if before is None:
                create_dropin(planned)
                changed = True
            system.reload()
            require(system.guard_directive(), 'managed_guard_not_effective')
            result['service_actions'] += 1
            system.action('restart', LEGACY)
            require(system.state(LEGACY) == expected[LEGACY], 'legacy_restart_not_healthy')
            for unit in TIMERS:
                expected[unit]['enabled'] = 'enabled'
                owned_states[unit].append(dict(expected[unit]))
                system.action('enable', unit)
                result['service_actions'] += 1
            for unit in TIMERS:
                timer_started = True
                expected[unit]['active'] = True
                owned_states[unit].append(dict(expected[unit]))
                system.action('start', unit)
                result['service_actions'] += 1
            require({u: system.state(u) for u in (LEGACY, *TIMERS)} == {u: expected[u] for u in (LEGACY, *TIMERS)}, 'activated_state_not_verified')
            result['calendars'] = {unit: system.calendar(unit, '08:15:00' if 'prepare' in unit else '09:00:00', now) for unit in TIMERS}
            if now.date().isoformat() < START_DATE:
                require(normal_ledger() == ledger_before, 'cutover_ledger_changed')
                result['cutover_ledger_unchanged'] = True
            require(image_matches(DROPIN, planned if changed else before), 'dropin_after_image_conflict')
            require(all(image_matches(path, image) for path, image in identities.values()), 'source_changed_after_activation')
            result['after_units'] = {u: system.state(u) for u in (LEGACY, *TIMERS)}
            result['activated'] = True
            private_json(target_backup / 'result.json', result)
        except BaseException as error:
            result.update(passed=False, activated=False, error_type=type(error).__name__,
                          error_code=str(error) if isinstance(error, Refused) else 'activation_failed')
            rollback_errors = []
            # Restore only owned states; preserve a foreign enabled/active image.
            for unit in reversed(TIMERS):
                try:
                    current = system.state(unit)
                    require(current in owned_states[unit], 'timer_after_image_conflict')
                    if current['active'] != states[unit]['active']:
                        system.action('start' if states[unit]['active'] else 'stop', unit)
                    if current['enabled'] != states[unit]['enabled']:
                        system.action('enable' if states[unit]['enabled'] == 'enabled' else 'disable', unit)
                    require(system.state(unit) == states[unit], 'timer_restore_unverified')
                except Exception as restore_error:
                    rollback_errors.append({'unit': unit, 'code': str(restore_error) if isinstance(restore_error, Refused) else type(restore_error).__name__})
            if timer_started:
                for unit in SERVICES:
                    try:
                        if not states[unit]['active'] and system.state(unit)['active']:
                            system.action('stop', unit)
                    except Exception as restore_error:
                        rollback_errors.append({'unit': unit, 'code': type(restore_error).__name__})
            try:
                if before is None and planned:
                    if not image_matches(DROPIN, None):
                        require(image_matches(DROPIN, planned), 'dropin_after_image_conflict')
                        DROPIN.unlink()
                        sync_dir(DROPIN.parent)
                else:
                    require(image_matches(DROPIN, before), 'dropin_before_image_conflict')
                system.reload()
                system.action('restart', LEGACY)
                require(system.state(LEGACY) == states[LEGACY], 'legacy_restore_unverified')
                if parent_created is not None:
                    info = DROPIN.parent.lstat()
                    require((info.st_dev, info.st_ino) == parent_created and not DROPIN.parent.is_symlink(), 'dropin_parent_changed')
                    DROPIN.parent.rmdir()  # Empty only; never recursive cleanup.
            except Exception as restore_error:
                rollback_errors.append({'unit': LEGACY, 'code': str(restore_error) if isinstance(restore_error, Refused) else type(restore_error).__name__})
            result['rollback_errors'] = rollback_errors
            result['rollback_verified'] = not rollback_errors
            try:
                private_json(target_backup / 'failed.json', result)
            except Exception as evidence_error:
                result['evidence_error_type'] = type(evidence_error).__name__
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--after-manifest', type=Path, required=True)
    parser.add_argument('--after-manifest-sha256', required=True)
    parser.add_argument('--timer-evidence', type=Path)
    parser.add_argument('--timer-evidence-sha256', default='')
    parser.add_argument('--client-receipt', type=Path)
    parser.add_argument('--client-receipt-sha256', default='')
    parser.add_argument('--client-ui-reviewed', action='store_true', help='Operator attests separately reviewing the hash-bound client evidence.')
    parser.add_argument('--activate', action='store_true', help='Write only the managed drop-in and activate the two timers; default is plan.')
    args = parser.parse_args(argv)
    try:
        require(os.name == 'posix', 'posix_host_required')
        require(not args.activate or os.geteuid() == 0, 'activation_requires_root')
        after, _ = checked_json(args.after_manifest, args.after_manifest_sha256)
        result = activate(after, timer=args.timer_evidence, timer_sha=args.timer_evidence_sha256,
                          client=args.client_receipt, client_sha=args.client_receipt_sha256,
                          client_ui_reviewed=args.client_ui_reviewed, apply=args.activate)
    except Exception as error:
        result = {'passed': False, 'mode': 'activate' if args.activate else 'plan',
                  'error_type': type(error).__name__, 'code': str(error) if isinstance(error, Refused) else 'preflight_failed',
                  'direct_messages_sent': 0, 'client_ui_automatically_verified': False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
