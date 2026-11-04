"""Prepare-only acceptance in a uniquely owned service-UID private stage.

The caller installs only this runner and the two pinned public modules. The
existing config and public ArticleInput schema are read in place. No delivery
transport can run; private source, configuration and article text never leave.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from types import ModuleType
from zoneinfo import ZoneInfo


EXPECTED = {
    "health_delivery.py": "91e5f649ff6773b08b0ba40e92656ed5457b42c811ac873c2a1d3302f1a3130d",
    "health_collector.py": "04e179b61df6d86e3c714c49fca297e9517fc01b8df674c083743d0fa87cf5be",
}
SCHEMA = Path('/opt/signaldesk-task/app/schemas.py')
SCHEMA_SHA = '6027d496258e73b7677dcbdd94cdb3454b102b59b201e0e3195f9ad84a3919ee'
CONFIG = Path('/etc/signaldesk/health-delivery.env')


class Discard:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


class NoDelivery:
    def __init__(self):
        self.calls = {"ingest": 0, "publish": 0, "cached": 0}

    def ingest(self, *args, **kwargs):
        self.calls['ingest'] += 1
        raise RuntimeError('Prepare acceptance forbids ingestion')

    def publish(self, *args, **kwargs):
        self.calls['publish'] += 1
        raise RuntimeError('Prepare acceptance forbids publication')

    def cached(self, *args, **kwargs):
        self.calls['cached'] += 1
        raise RuntimeError('Prepare acceptance forbids receipt access')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metadata(path):
    info = path.lstat()
    if path.is_symlink():
        raise ValueError('Unexpected symlink in owned stage')
    return {"uid": info.st_uid, "gid": info.st_gid,
            "mode": oct(stat.S_IMODE(info.st_mode)), "bytes": info.st_size}


def atomic_metadata(path, value):
    temporary = path.with_name('.' + path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        if path.exists() or path.is_symlink():
            raise ValueError('Acceptance result already exists')
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run(stage, task_parser=False):
    if os.name != 'posix' or os.geteuid() != 994 or os.getegid() != 990:
        raise PermissionError('Acceptance requires the existing service UID/GID')
    if (stage.parent != Path('/var/lib') or not re.fullmatch(r'signaldesk-health-rss-acceptance-[a-f0-9]{12}', stage.name)
            or stage.is_symlink() or stage.resolve() != stage):
        raise ValueError('Acceptance stage mismatch')
    info = metadata(stage)
    if info['uid'] != 994 or info['gid'] != 990 or info['mode'] != '0o700':
        raise PermissionError('Acceptance stage must already be private')
    os.umask(0o077)
    sys.dont_write_bytecode = True
    runtime = stage / 'runtime'
    app = runtime / 'app'
    source_shas = {}
    for name, expected in EXPECTED.items():
        path = app / name
        source_info = metadata(path)
        if (source_info['mode'] != '0o600' or source_info['uid'] != 994 or source_info['gid'] != 990
                or digest(path) != expected):
            raise ValueError('Acceptance public source mismatch')
        source_shas[name] = expected
    if SCHEMA.is_symlink() or digest(SCHEMA) != SCHEMA_SHA:
        raise ValueError('Existing public ArticleInput schema mismatch')
    package = ModuleType('app')
    package.__path__ = [str(app), str(SCHEMA.parent)]
    sys.modules['app'] = package
    sys.path.insert(0, str(runtime))
    module = importlib.import_module('app.health_delivery')
    if Path(module.__file__).resolve() != (app / 'health_delivery.py').resolve():
        raise ValueError('Worker import escaped stage')
    before = digest(CONFIG)
    config = replace(module.Config.load(CONFIG), max_items=3)
    if task_parser:
        config = replace(config, collector_python=sys.executable)
    no_delivery = NoDelivery()
    run_id = 'rss-' + stage.name.rsplit('-', 1)[-1]
    worker = module.Worker(stage / 'state', module.Collector(Path('/opt/signaldesk'), config),
                           no_delivery, test_run=True, run_id=run_id, start_date=config.start_date)
    now = datetime.now(timezone.utc)
    report = {"passed": False, "phase": "prepare_only", "real_provider_content": True,
              "stage": str(stage), "run_id": run_id, "uid": os.geteuid(), "gid": os.getegid(),
              "started_utc": now.isoformat(), "started_shanghai": now.astimezone(ZoneInfo('Asia/Shanghai')).isoformat(),
              "clock_override": False, "max_items_override_in_memory": 3, "source_sha256": source_shas,
              "task_parser_override_in_memory": task_parser,
              "existing_public_schema_sha256": SCHEMA_SHA,
              "private_source_or_config_or_article_text_exported": False,
              "production_deployed": False, "business_database_written": False, "messages_sent": 0}
    subprocesses = []
    original_run = module.subprocess.run

    def observed(command, *positional, **keywords):
        if len(command) >= 2 and Path(command[1]).name == 'health_collector.py':
            phase = 'public_rss'
        elif len(command) >= 3 and command[1:3] == ['-c', module.COLLECTOR_ADAPTER]:
            phase = 'private_parser'
        else:
            raise ValueError('Unexpected prepare subprocess')
        try:
            process = original_run(command, *positional, **keywords)
        except OSError as error:
            subprocesses.append({'phase': phase, 'error_type': type(error).__name__, 'errno': error.errno})
            raise
        def as_bytes(value):
            return value if isinstance(value, bytes) else (value or '').encode('utf-8', 'replace')
        stdout, stderr = as_bytes(process.stdout), as_bytes(process.stderr)
        entry = {'phase': phase, 'returncode': process.returncode,
                 'stdout_bytes': len(stdout), 'stderr_bytes': len(stderr)}
        if process.returncode:
            private = stage / 'diagnostics'
            private.mkdir(mode=0o700, exist_ok=True)
            private.chmod(0o700)
            for suffix, data in (('stdout', stdout), ('stderr', stderr)):
                fd = os.open(private / (phase + '.' + suffix + '.private.log'),
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
            text = stderr.decode('utf-8', 'replace')
            allowed = ('ModuleNotFoundError', 'ImportError', 'PermissionError', 'FileNotFoundError',
                       'UnicodeEncodeError', 'UnicodeDecodeError', 'ValueError', 'RuntimeError', 'TypeError')
            entry['error_categories'] = [name for name in allowed if re.search(r'\b' + name + r'\b', text)]
            entry['missing_modules'] = sorted(set(re.findall(
                r'''ModuleNotFoundError: No module named ['"]([A-Za-z_][A-Za-z0-9_.]{0,100})['"]''', text)))
            entry['permission_errno'] = sorted(set(int(value) for value in re.findall(
                r'PermissionError: \[Errno ([0-9]{1,3})\]', text)))
            entry['raw_output_exported'] = False
        subprocesses.append(entry)
        return process

    module.subprocess.run = observed
    try:
        outcome = worker.prepare()
        report['prepare'] = {key: value for key, value in outcome.items()
                             if key in ('state', 'articles', 'batches', 'reused')}
        collected = worker.state / 'collected' / ('test-' + run_id)
        summary = json.loads((collected / 'summary.json').read_text(encoding='utf-8'))
        # This source-pinned public summary contains only metadata. A URL/text
        # assertion catches any accidental change before it is exported.
        serialized = json.dumps(summary, ensure_ascii=True)
        if 'http://' in serialized or 'https://' in serialized:
            raise ValueError('Unexpected URL in provider summary')
        report['collector_summary'] = summary
        snapshot = worker.load_snapshot()
        md = collected / (worker.day + '.md')
        report.update(date=worker.day, article_count=len(snapshot['articles']), body_packet_count=len(snapshot['bodies']),
                      snapshot_sha256=snapshot['sha256'], markdown_sha256=digest(md), markdown_bytes=md.stat().st_size,
                      body_packet_sha256=[module.sha(value) for value in snapshot['bodies']],
                      markdown_metadata=metadata(md), snapshot_metadata=metadata(worker.snapshot),
                      output_metadata=metadata(collected), state_metadata=metadata(worker.state),
                      test_id_namespace=all(value['id'].startswith('test:' + run_id + ':') for value in snapshot['articles']),
                      test_title_prefix=all(value['title'].startswith('[测试 ' + run_id + '] ') for value in snapshot['articles']),
                      ledger_created=(worker.state / 'ledger.sqlite3').exists())
        report['passed'] = (outcome.get('state') == 'prepared' and 1 <= report['article_count'] <= 3
                            and summary.get('article_count') == report['article_count']
                            and report['body_packet_count'] >= 1 and not report['ledger_created']
                            and report['test_id_namespace'] and report['test_title_prefix'])
        report['private_permissions_valid'] = all(
            report[key]['uid'] == 994 and report[key]['gid'] == 990 and report[key]['mode'] == mode
            for key, mode in (('markdown_metadata', '0o600'), ('snapshot_metadata', '0o600'),
                              ('output_metadata', '0o700'), ('state_metadata', '0o700')))
        report['passed'] = report['passed'] and report['private_permissions_valid']
    except Exception as error:
        report['failure_type'] = type(error).__name__
        summary = worker.state / 'collected' / ('test-' + run_id) / 'summary.json'
        if summary.is_file() and not summary.is_symlink():
            value = json.loads(summary.read_text(encoding='utf-8'))
            if 'http://' not in json.dumps(value) and 'https://' not in json.dumps(value):
                report['collector_summary'] = value
    finally:
        module.subprocess.run = original_run
        report['subprocesses'] = subprocesses
        report['transport_calls'] = no_delivery.calls
        report['private_config_unchanged'] = digest(CONFIG) == before
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        if any(no_delivery.calls.values()) or not report['private_config_unchanged']:
            report['passed'] = False
    # Every persisted payload remains private. Only this metadata is exported.
    atomic_metadata(stage / 'prepare-metadata.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--task-parser', action='store_true',
                        help='Explicit diagnosis only: use Task runtime for the old pure parsing contract')
    args = parser.parse_args(argv)
    try:
        with redirect_stdout(Discard()), redirect_stderr(Discard()):
            report = run(args.stage, args.task_parser)
    except Exception as error:
        report = {"passed": False, "phase": "acceptance_guard", "error_type": type(error).__name__,
                  "raw_output_exported": False, "business_database_written": False, "messages_sent": 0}
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
