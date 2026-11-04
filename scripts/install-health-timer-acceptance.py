"""Plan/install one private synthetic timer test; no production timer changes.

Run the reviewed script with the Task Python3.10 on its managed host. All
mutations need --apply. No clock override, arbitrary destination or config flag.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from zoneinfo import ZoneInfo

TASK = Path('/opt/signaldesk-task')
PYTHON = TASK / '.venv/bin/python'
CONFIG = Path('/etc/signaldesk/health-delivery.env')
ENVIRONMENT = TASK / '.env'
STATE_PARENT = Path('/var/lib')
UNITS = Path('/etc/systemd/system')
PREFIX = 'signaldesk-health-timer-acceptance-'
UID, GID = 994, 990
SHANGHAI = ZoneInfo('Asia/Shanghai')
SCHEMA = 'signaldesk.health-timer-acceptance.v1'
MEMBERS = ('app/health_delivery.py', 'app/health_collector.py')

RUNNER = r'''"""Private single synthetic item; credentials never enter output/state."""
import argparse,hashlib,json,os,re,socket,sqlite3,sys,tempfile,time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE=Path(__file__).parent
TASK=Path('/opt/signaldesk-task')
CONFIG=Path('/etc/signaldesk/health-delivery.env')
ZONE=ZoneInfo('Asia/Shanghai')
BODY='健康管理商业情报定时推送验收：本条为单篇合成验收内容，不代表真实新闻。'

def sha(value):return hashlib.sha256(value.encode()).hexdigest()

def atomic(path,value):
 fd,name=tempfile.mkstemp(prefix='.acceptance-',dir=path.parent)
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as stream:
   json.dump(value,stream,ensure_ascii=True,sort_keys=True);stream.flush();os.fsync(stream.fileno())
  os.chmod(name,0o600);os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)

def load_service(manifest):
 for name,expected in manifest['source_sha256'].items():
  path=BASE/'code'/Path(name).name
  if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('public_stage_sha_mismatch')
 sys.path.insert(0,str(TASK))
 import app
 app.__path__.insert(0,str(BASE/'code'))
 from app import health_delivery
 if Path(health_delivery.__file__)!=BASE/'code/health_delivery.py':raise ValueError('wrong_worker_module')
 return health_delivery

def fixture(config,day):
 title='健康管理商业情报 | '+day+' [1/1]'
 markdown='### 1. '+title+'\n'+BODY+'\n'
 article={'id':'synthetic-timer:'+day,'channelId':'health','title':title,'body':BODY,
  'timestamp':int(datetime.now(ZONE).timestamp()*1000),'source':re.sub(r'^https?://','',config.server),
  'clickUrl':None,'tags':['green_book'],'priority':3}
 return {'markdown':markdown,'bodies':[markdown],'articles':[article]}

def ledger(worker):
 with worker.connection() as db:
  rows=db.execute('SELECT status,attempts,ntfy_id FROM batches WHERE run_key=?',(worker.run_key,)).fetchall()
  return {'batch_rows':len(rows),'ack_count':sum(row['status']=='sent' and bool(row['ntfy_id']) for row in rows),
   'attempts_total':sum(row['attempts'] for row in rows),
   'ack_sha256':sha(json.dumps(sorted(row['ntfy_id'] for row in rows if row['status']=='sent')))}

def execute(mode,service=None):
 if os.getuid()!=994 or os.getgid()!=990:raise ValueError('wrong_runtime_identity')
 manifest=json.loads((BASE/'manifest.json').read_text())
 today=datetime.now(ZONE).date().isoformat()
 if manifest['date']!=today or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,63}',manifest['run_id']):raise ValueError('wrong_acceptance_day_or_id')
 service=service or load_service(manifest)
 config=service.Config.load(CONFIG)
 data=fixture(config,today)
 prefix='[测试 '+manifest['run_id']+'] '
 expected_title=prefix+data['articles'][0]['title']
 expected_body=data['bodies'][0].replace('### 1. ','### 1. '+prefix,1)
 calls={'ingest_calls':0,'publish_calls':0,'cache_calls':0}
 class Synthetic:
  def prepare(self,*args):return data
 class Closed:
  def ingest(self,*args):raise RuntimeError('prepare_transport_forbidden')
  def publish(self,*args,**kwargs):raise RuntimeError('prepare_transport_forbidden')
  def cached(self,*args):raise RuntimeError('prepare_transport_forbidden')
 class Counted(service.Transport):
  def ingest(self,items):
   if len(items)!=1 or items[0]['title']!=expected_title or items[0]['body']!=BODY or items[0]['source']!=data['articles'][0]['source'] or items[0]['clickUrl'] is not None:raise ValueError('synthetic_ingest_mismatch')
   calls['ingest_calls']+=1;return super().ingest(items)
  def publish(self,title,body,tags=None):
   if title!=expected_title or body!=expected_body or tags is not None:raise ValueError('synthetic_publish_mismatch')
   calls['publish_calls']+=1;return super().publish(title,body)
  def cached(self,since):
   calls['cache_calls']+=1;return super().cached(since)
 worker=service.Worker(BASE/'worker-state',Synthetic(),Counted(config) if mode=='deliver' else Closed(),
   test_run=True,run_id=manifest['run_id'],start_date=config.start_date)
 if mode=='prepare':
  if worker.snapshot.exists():raise ValueError('prepare_requires_new_snapshot')
  old_connect,old_connect_ex=socket.socket.connect,socket.socket.connect_ex
  def no_network(*args,**kwargs):raise RuntimeError('prepare_network_forbidden')
  try:
   socket.socket.connect=socket.socket.connect_ex=no_network
   result=worker.prepare()
  finally:socket.socket.connect,socket.socket.connect_ex=old_connect,old_connect_ex
  snapshot=worker.load_snapshot()
  if result['articles']!=1 or len(snapshot['bodies'])!=1 or snapshot['articles'][0]['title']!=expected_title or snapshot['bodies'][0]!=expected_body:raise ValueError('prepared_fixture_mismatch')
  record={'articles':1,'batches':1,'ingest_calls':0,'publish_calls':0,'clock_override':False,
   'title_sha256':sha(expected_title),'body_sha256':sha(expected_body),'source_sha256':sha(data['articles'][0]['source'])}
  atomic(BASE/'prepared.json',record);return record
 snapshot=worker.load_snapshot()
 if len(snapshot['articles'])!=1 or snapshot['articles'][0]['title']!=expected_title or snapshot['articles'][0]['body']!=BODY or snapshot['articles'][0]['source']!=data['articles'][0]['source'] or snapshot['articles'][0]['clickUrl'] is not None or snapshot['bodies']!=[expected_body]:raise ValueError('frozen_synthetic_snapshot_required')
 if mode=='status':return ledger(worker)
 if not (BASE/'prepared.json').is_file():raise ValueError('prepare_receipt_required')
 records=sorted(BASE.glob('delivery-[12].json'))
 if len(records)>=2:raise ValueError('two_deliveries_only')
 result=worker.deliver()
 record={'monotonic_usec':int(time.monotonic()*1000000),'recorded_at':datetime.now(ZONE).isoformat(),
  'state':result['state'],'articles':result.get('articles',0),**calls,**ledger(worker)}
 atomic(BASE/('delivery-'+str(len(records)+1)+'.json'),record)
 return record

def main(argv=None):
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True)
 for value in ('prepare','deliver','status'):g.add_argument('--'+value,action='store_true')
 a=p.parse_args(argv)
 try:
  result=execute('prepare' if a.prepare else 'deliver' if a.deliver else 'status')
  print(json.dumps(result,sort_keys=True));return 0 if result.get('state','sent')=='sent' else 1
 except Exception as error:
  print(json.dumps({'passed':False,'error_type':type(error).__name__}));return 1
if __name__=='__main__':raise SystemExit(main())
'''


class Refused(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe(path, absent=False):
    for parent in reversed(path.parents):
        metadata = parent.lstat()
        require(stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode), 'unsafe_parent')
    if absent:
        require(not path.exists() and not path.is_symlink(), 'new_path_required')
    else:
        require(not path.is_symlink(), 'symlink_rejected')
        path.lstat()


def command(parts, *, timeout=30):
    result = subprocess.run(parts, capture_output=True, text=True, timeout=timeout,
        cwd=TASK,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
             'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'})
    require(result.returncode == 0, 'command_failed')
    return result.stdout


def clock_contract():
    version = command(['systemctl', '--version']).splitlines()[0]
    require(version.split()[:2] == ['systemd', '219'], 'systemd219_required')
    status = command(['timedatectl', 'status'])
    require(re.findall(r'^\s*Time zone:\s+(\S+)', status, re.M) == ['Asia/Shanghai'], 'shanghai_manager_required')
    require(re.findall(r'^\s*NTP synchronized:\s+(yes|no)', status, re.M) == ['yes'], 'synchronized_clock_required')


def identity(path):
    safe(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == info.st_gid == 0
            and stat.S_IMODE(info.st_mode) == 0o644, 'root_unit_metadata_required')
    return {'inode': info.st_ino, 'device': info.st_dev, 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode), 'sha256': sha(path.read_bytes())}


def write(path, data, uid, gid, mode, planned=None):
    safe(path, absent=not path.exists())
    descriptor, name = tempfile.mkstemp(prefix='.health-acceptance-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data); stream.flush()
            os.chown(name, uid, gid); os.chmod(name, mode); os.fsync(stream.fileno())
            if planned is not None:
                info = os.fstat(stream.fileno())
                planned[path.name] = {'inode': info.st_ino, 'device': info.st_dev, 'uid': info.st_uid,
                    'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode), 'sha256': sha(data)}
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        if os.path.exists(name): os.unlink(name)


def unit_text(state):
    service = ('[Unit]\nDescription=Single synthetic health timer acceptance\n'
        'After=network-online.target signaldesk-task-api.service\n\n[Service]\nType=oneshot\n'
        'User=994\nGroup=990\nWorkingDirectory=/opt/signaldesk-task\n'
        'EnvironmentFile=/opt/signaldesk-task/.env\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n'
        'Environment=PYTHONUNBUFFERED=1\nUMask=0077\n'
        f'ExecStart={PYTHON} -B {state}/runner.py --deliver\n'
        'TimeoutStartSec=90s\nNoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=full\n'
        f'ProtectHome=true\nReadWriteDirectories={state}\n')
    return service


def schedule(now):
    require(now.tzinfo is not None, 'timezone_required')
    target = datetime.fromtimestamp(math.ceil(now.timestamp()) + 120, SHANGHAI)
    require(target.date() == now.astimezone(SHANGHAI).date()
            and target.hour >= 9, 'acceptance_window_requires_same_day_after0900')
    return target


def timer_text(service_name, target):
    return ('[Unit]\nDescription=One real-clock synthetic health acceptance\n\n[Timer]\n'
            f'OnCalendar={target.strftime("%Y-%m-%d %H:%M:%S")}\nAccuracySec=1s\n'
            f'Persistent=false\nUnit={service_name}\n')


def state_for(run_id):
    require(re.fullmatch('[a-z0-9][a-z0-9-]{1,63}', run_id) is not None, 'safe_hyphen_run_id_required')
    return STATE_PARENT / (PREFIX + run_id)


def manifest_for(run_id, expected):
    clock_contract()
    state = state_for(run_id)
    safe(state, absent=True)
    require(set(expected) == set(MEMBERS) and all(re.fullmatch('[0-9a-f]{64}', value) for value in expected.values()), 'sha_manifest_required')
    for name, checksum in expected.items():
        path = TASK / name; safe(path)
        require(path.is_file() and sha(path.read_bytes()) == checksum, 'live_public_source_sha_mismatch')
    for path in (CONFIG, ENVIRONMENT):
        safe(path); require(path.is_file(), 'fixed_private_config_missing')
    target = schedule(datetime.now(SHANGHAI))
    service_name = PREFIX + run_id + '.service'
    timer_name = PREFIX + run_id + '.timer'
    for name in (service_name, timer_name): safe(UNITS / name, absent=True)
    service, timer = unit_text(state), timer_text(service_name, target)
    return {'schema': SCHEMA, 'passed': False, 'phase': 'install_pending', 'state_dir': str(state),
        'run_id': run_id, 'test_run': True, 'clock_override': False, 'manager_timezone': 'Asia/Shanghai',
        'ntp_synchronized': True, 'scheduled_at': target.isoformat(), 'date': target.date().isoformat(),
        'on_calendar': target.strftime('%Y-%m-%d %H:%M:%S'), 'service_unit': service_name, 'timer_unit': timer_name,
        'source_sha256': expected, 'artifact_sha256': {'runner': sha(RUNNER.encode()), 'unit': sha(service.encode()), 'timer': sha(timer.encode())},
        'production_timer_actions': 0}


def run_as_task(state, mode):
    import grp
    import pwd
    account, group = pwd.getpwuid(UID), grp.getgrgid(GID)
    require(account.pw_gid == GID, 'task_primary_group_mismatch')
    output = command(['runuser', '-u', account.pw_name, '-g', group.gr_name, '--', str(PYTHON), '-B', str(state / 'runner.py'), '--' + mode], timeout=90)
    return json.loads(output)


def install(run_id, expected, apply=False):
    manifest = manifest_for(run_id, expected)
    if not apply:
        return {**manifest, 'mode': 'dry_run'}
    state = state_for(run_id)
    state.mkdir(mode=0o700); os.chown(state, UID, GID); os.chmod(state, 0o700)
    code = state / 'code'; code.mkdir(mode=0o750); os.chown(code, 0, GID); os.chmod(code, 0o750)
    created_units = {}
    try:
        for name, checksum in expected.items():
            data = (TASK / name).read_bytes()
            require(sha(data) == checksum, 'source_changed_before_stage')
            compile(data, name, 'exec')
            write(code / Path(name).name, data, 0, GID, 0o440)
        compile(RUNNER, 'acceptance_runner', 'exec')
        write(state / 'runner.py', RUNNER.encode(), 0, GID, 0o440)
        write(state / 'manifest.json', json.dumps(manifest, sort_keys=True).encode(), 0, GID, 0o440)
        prepared = run_as_task(state, 'prepare')
        require(prepared.get('articles') == prepared.get('batches') == 1
                and prepared.get('ingest_calls') == prepared.get('publish_calls') == 0, 'prepare_receipt_invalid')
        # The actual 120-second timer is calculated AFTER preparation completes.
        target = schedule(datetime.now(SHANGHAI))
        manifest.update(scheduled_at=target.isoformat(), on_calendar=target.strftime('%Y-%m-%d %H:%M:%S'))
        texts = {'unit': unit_text(state), 'timer': timer_text(manifest['service_unit'], target)}
        manifest['artifact_sha256'].update({key: sha(value.encode()) for key, value in texts.items()})
        write(state / 'manifest.json', json.dumps(manifest, sort_keys=True).encode(), 0, GID, 0o440)
        for key, name in [('unit', manifest['service_unit']), ('timer', manifest['timer_unit'])]:
            path = UNITS / name; safe(path, absent=True)
            write(path, texts[key].encode(), 0, 0, 0o644, planned=created_units)
        # Persist the exact created unit image before any start. Content alone
        # cannot distinguish a same-byte replacement by another operator.
        manifest['unit_identity'] = created_units
        write(state / 'manifest.json', json.dumps(manifest, sort_keys=True).encode(), 0, GID, 0o440)
        command(['systemd-analyze', 'verify', *[str(UNITS / name) for name in created_units]])
        command(['systemctl', 'daemon-reload'])
        command(['systemctl', 'start', '--no-block', manifest['timer_unit']])
        return {**manifest, 'phase': 'awaiting_timer', 'prepare': prepared, 'timer_start_requested': True}
    except BaseException:
        # Never recurse/delete the private state/ledger. Units are removable
        # only while their inode+SHA still equal our exact created image.
        owned = []
        for name, expected_image in created_units.items():
            path = UNITS / name
            try:
                if identity(path) == expected_image: owned.append(name)
            except (OSError, Refused): pass
        if owned:
            # A lost start ACK may still leave the temporary timer active.
            # Stop only these unique test units before removing our files.
            command(['systemctl', 'stop', *owned])
        for name in owned:
            path = UNITS / name
            if identity(path) == created_units[name]: path.unlink()
        command(['systemctl', 'daemon-reload'])
        raise


def read_manifest(run_id):
    state = state_for(run_id); safe(state)
    metadata = state.lstat()
    require(metadata.st_uid == UID and metadata.st_gid == GID and stat.S_IMODE(metadata.st_mode) == 0o700, 'private_state_identity_required')
    path = state / 'manifest.json'; safe(path)
    def root_public(file):
        safe(file)
        info = file.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0
                and info.st_gid == GID and stat.S_IMODE(info.st_mode) == 0o440, 'root_public_stage_required')
    root_public(path)
    manifest = json.loads(path.read_text())
    require(manifest['schema'] == SCHEMA and manifest['state_dir'] == str(state)
            and manifest['run_id'] == run_id and manifest['test_run'] is True and manifest['clock_override'] is False,
            'manifest_identity_mismatch')
    require(manifest['manager_timezone'] == 'Asia/Shanghai' and manifest['ntp_synchronized'] is True
            and manifest['production_timer_actions'] == 0, 'fixed_clock_contract_required')
    require(manifest['service_unit'] == PREFIX + run_id + '.service'
            and manifest['timer_unit'] == PREFIX + run_id + '.timer', 'fixed_test_units_required')
    require(set(manifest['source_sha256']) == set(MEMBERS)
            and all(re.fullmatch('[0-9a-f]{64}', value) for value in manifest['source_sha256'].values()),
            'fixed_source_manifest_required')
    target = datetime.fromisoformat(manifest['scheduled_at'])
    require(target.utcoffset() == timedelta(hours=8) and target.date().isoformat() == manifest['date']
            and manifest['on_calendar'] == target.strftime('%Y-%m-%d %H:%M:%S'), 'calendar_identity_mismatch')
    required_artifacts = {'runner': sha(RUNNER.encode()), 'unit': sha(unit_text(state).encode()),
                          'timer': sha(timer_text(manifest['service_unit'], target).encode())}
    require(manifest['artifact_sha256'] == required_artifacts, 'fixed_artifacts_required')
    require(set(manifest.get('unit_identity', {})) == {manifest['service_unit'], manifest['timer_unit']},
            'unit_identity_manifest_required')
    for key, path in [('runner', state / 'runner.py'), ('unit', UNITS / manifest['service_unit']), ('timer', UNITS / manifest['timer_unit'])]:
        safe(path); require(path.is_file() and sha(path.read_bytes()) == manifest['artifact_sha256'][key], 'artifact_sha_mismatch')
        if key == 'runner': root_public(path)
        else: require(identity(path) == manifest['unit_identity'][path.name], 'unit_afterimage_changed')
    for name, expected in manifest['source_sha256'].items():
        path = state / 'code' / Path(name).name; safe(path)
        root_public(path)
        require(path.is_file() and sha(path.read_bytes()) == expected, 'staged_source_sha_mismatch')
    return state, manifest


def properties(unit, names):
    output = command(['systemctl', 'show', unit, *['--property=' + name for name in names]])
    return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)


def timer_monotonic_usec(unit):
    # systemctl219 formats this uint64 as an approximate human timespan, e.g.
    # "1month ...". D-Bus exposes the actual scalar; never infer month lengths.
    response = command(['busctl', 'call', 'org.freedesktop.systemd1', '/org/freedesktop/systemd1',
                        'org.freedesktop.systemd1.Manager', 'GetUnit', 's', unit])
    path = re.fullmatch(r'o "([A-Za-z0-9_/]+)"\s*', response)
    require(path is not None, 'typed_timer_unit_path_required')
    scalar = command(['busctl', 'get-property', 'org.freedesktop.systemd1', path[1],
                      'org.freedesktop.systemd1.Timer', 'LastTriggerUSecMonotonic'])
    value = re.fullmatch(r't ([0-9]+)\s*', scalar)
    require(value is not None and int(value[1]) < 2**64, 'typed_timer_uint64_required')
    return int(value[1])


def inspect(run_id):
    clock_contract()
    state, manifest = read_manifest(run_id)
    service = properties(manifest['service_unit'], ['Result', 'ExecMainStatus', 'ActiveState'])
    trigger = timer_monotonic_usec(manifest['timer_unit'])
    records = []
    for index in (1, 2):
        path = state / f'delivery-{index}.json'
        if path.exists():
            safe(path); records.append(json.loads(path.read_text()))
    output = {**manifest, 'timer': {'last_trigger_monotonic_usec': trigger},
              'service': {'result': service.get('Result'), 'exec_main_status': int(service.get('ExecMainStatus', '-1')),
                          'active_state': service.get('ActiveState')}, 'synthetic_only': False,
              'phase': 'awaiting_timer', 'passed': False, 'dedupe_verified': False}
    now = datetime.now(SHANGHAI)
    fired = trigger > 0 and datetime.fromisoformat(manifest['scheduled_at']) <= now
    finished = service.get('Result') == 'success' and service.get('ExecMainStatus') == '0' and service.get('ActiveState') == 'inactive'
    if records:
        first = records[0]; output['first_delivery'] = first
        first_ok = all(first.get(name) == 1 for name in ('articles', 'batch_rows', 'ack_count', 'attempts_total', 'ingest_calls', 'publish_calls'))
        first_ok = first_ok and first.get('state') == 'sent' and first.get('monotonic_usec', 0) >= trigger
        first_ok = first_ok and re.fullmatch('[0-9a-f]{64}', first.get('ack_sha256', '')) is not None
        if fired and finished and first_ok:
            output['phase'] = 'awaiting_second'
            if len(records) == 2:
                second = records[1]; output['second_delivery'] = second
                same = all(second.get(name) == first.get(name) for name in ('articles', 'batch_rows', 'ack_count', 'attempts_total', 'ack_sha256'))
                valid = (same and second.get('ingest_calls') == second.get('publish_calls') == 0
                         and second.get('state') == 'sent' and second.get('monotonic_usec', 0) >= first['monotonic_usec'])
                if valid: output.update(phase='verified', passed=True, dedupe_verified=True)
    return output


def second(run_id, apply=False):
    result = inspect(run_id)
    require(result['phase'] == 'awaiting_second', 'real_timer_first_receipt_required')
    if apply:
        command(['systemctl', 'start', '--no-block', result['service_unit']])
    return {**result, 'second_start_requested': apply, 'mode': 'apply' if apply else 'dry_run'}


def cleanup(run_id, apply=False):
    result = inspect(run_id)
    # Failed/unknown tests also need a safe stop. Preserve every private
    # snapshot/ACK/ledger; cleanup never upgrades a failed result to PASS.
    if apply:
        command(['systemctl', 'stop', result['timer_unit'], result['service_unit']])
        # Revalidate after systemctl returns; preserve externally changed files.
        _, manifest = read_manifest(run_id)
        for name in (manifest['timer_unit'], manifest['service_unit']): (UNITS / name).unlink()
        command(['systemctl', 'daemon-reload'])
    return {**result, 'test_units_removed': apply, 'private_ledger_preserved': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['install', 'inspect', 'second-deliver', 'cleanup'], default='install')
    parser.add_argument('--run-id')
    parser.add_argument('--worker-sha256')
    parser.add_argument('--collector-sha256')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        require(os.name == 'posix' and os.geteuid() == 0, 'managed_root_host_required')
        require(sys.version_info[:2] == (3, 10), 'task_python310_required')
        os.umask(0o077)
        if args.mode == 'install':
            run_id = args.run_id or 'timer-' + datetime.now(SHANGHAI).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:10]
            result = install(run_id, dict(zip(MEMBERS, (args.worker_sha256 or '', args.collector_sha256 or ''))), args.apply)
        else:
            require(args.run_id is not None, 'run_id_required')
            result = {'inspect': inspect, 'second-deliver': second, 'cleanup': cleanup}[args.mode](args.run_id, **({} if args.mode == 'inspect' else {'apply': args.apply}))
        print(json.dumps(result, sort_keys=True)); return 0
    except Exception as error:
        print(json.dumps({'schema': SCHEMA, 'passed': False, 'error_type': type(error).__name__,
                          'code': str(error) if isinstance(error, Refused) else 'operation_failed'})); return 1


if __name__ == '__main__':
    raise SystemExit(main())
