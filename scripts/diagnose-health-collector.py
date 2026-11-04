"""Stage-only health collector diagnosis. No private source or log export.

Default inspect is static metadata only. prepare explicitly reruns the public
Worker under the operator's existing UID994/systemd sandbox. Raw failed process
output stays private on that host; no publishing or article ingestion is allowed.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

STAGE = Path('/var/lib/signaldesk-health-preflight-8974d6ef4d09')
COLLECTOR = Path('/opt/signaldesk')
RUN_ID = 'collector-8974d6ef4d09'
WORKER_SHA = '9c7a8995c26456aef4cab1d24b9e5f9bed490a1735279a4a9fffc0f8bb20138d'
ERRORS = {'PermissionError', 'FileNotFoundError', 'NotADirectoryError', 'IsADirectoryError',
          'ModuleNotFoundError', 'ImportError', 'ValueError', 'TypeError', 'RuntimeError',
          'OSError', 'AttributeError', 'KeyError', 'JSONDecodeError', 'SyntaxError',
          'HTTPError', 'HTTPStatusError', 'ConnectError', 'ConnectTimeout', 'ReadTimeout',
          'WriteTimeout', 'TimeoutExpired', 'CalledProcessError', 'SSLError'}
SENSITIVE = re.compile(r'(^|[/_.-])(env|secrets?|tokens?|credentials?|passwords?|api[_-]?keys?|keys?|sk|auth|ssh|aws|bailian|id_rsa|id_ed25519)([/_.-]|$)', re.I)


def identity_guard() -> None:
    if os.name != 'posix' or os.geteuid() != 994:
        raise PermissionError('Diagnostic must run as the existing service UID994')
    metadata = STAGE.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or STAGE.is_symlink() or STAGE.resolve() != STAGE:
        raise ValueError('Unexpected stage root')
    if metadata.st_uid != 994 or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise PermissionError('Stage must already be owned by UID994 and mode0700')


def private_file(path: Path, data: bytes) -> None:
    if not path.resolve().is_relative_to(STAGE) or path.exists() or path.is_symlink():
        raise ValueError('Unsafe diagnostic output')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
    path.chmod(0o600)


def private_directory() -> Path:
    parent = STAGE / 'diagnostics'
    if parent.exists():
        metadata = parent.lstat()
        if parent.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 994 or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise PermissionError('Diagnostic parent must be private')
    else:
        parent.mkdir(mode=0o700)
    target = parent / ('collector-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    target.mkdir(mode=0o700)
    return target


def path_category(path: Path) -> str:
    text = str(path)
    if path.is_relative_to(STAGE):
        return 'isolated_stage'
    if path.is_relative_to(COLLECTOR):
        return 'collector_project'
    if text.startswith(('/home/', '/root/')):
        return 'home_protected_by_sandbox'
    if text.startswith(('/tmp/', '/var/tmp/')):
        return 'private_tmp_namespace'
    if text.startswith(('/usr/', '/etc/')):
        return 'system_path'
    return 'other_location_redacted'


def public_path(path: Path) -> dict:
    # Only these non-sensitive path classes may leave the server. Never show a
    # source line, URL/query, env filename, key/token filename or article title.
    text = str(path)
    category = path_category(path)
    safe = category in {'isolated_stage', 'collector_project', 'private_tmp_namespace'}
    safe = safe and not SENSITIVE.search(text) and bool(re.fullmatch(r'[A-Za-z0-9_./*?\[\]-]{1,500}', text))
    value = {'category': category, 'path_redacted': not safe}
    if safe:
        value['path'] = text
    return value


def path_metadata(path: Path, role: str, symbol: str = '') -> dict:
    path = Path(os.path.normpath(str(path if path.is_absolute() else COLLECTOR / path)))
    value = {**public_path(path), 'role': role}
    if symbol and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', symbol):
        value['symbol'] = symbol
    check = path
    pattern = any(char in str(check) for char in '*?[')
    if pattern:
        parts = check.parts
        index = next(index for index, part in enumerate(parts) if any(char in part for char in '*?['))
        check = Path(*parts[:index])
        value['pattern_parent_only'] = True
    try:
        metadata = check.stat()
        value.update(exists=True, is_directory=stat.S_ISDIR(metadata.st_mode),
                     uid=metadata.st_uid, gid=metadata.st_gid, mode=oct(stat.S_IMODE(metadata.st_mode)),
                     readable=os.access(check, os.R_OK), writable=os.access(check, os.W_OK),
                     searchable=os.access(check, os.X_OK) if stat.S_ISDIR(metadata.st_mode) else None)
        if role in {'read', 'source_glob'} and stat.S_ISDIR(metadata.st_mode) and os.access(check, os.R_OK | os.X_OK):
            total = readable = 0
            # File names and contents are never exported, only a bounded count.
            with os.scandir(check) as entries:
                for entry in entries:
                    total += 1
                    if entry.is_file(follow_symlinks=False) and os.access(entry.path, os.R_OK):
                        readable += 1
                    if total >= 64:
                        break
            value['bounded_entries_seen'] = total
            value['bounded_readable_files'] = readable
    except OSError as error:
        value.update(exists=False, error_type=type(error).__name__ if type(error).__name__ in ERRORS else 'OSError')
        ancestor = check.parent
        for _ in range(12):
            if ancestor.exists():
                value['nearest_parent'] = public_path(ancestor)
                value['parent_writable'] = os.access(ancestor, os.W_OK | os.X_OK)
                break
            if ancestor == ancestor.parent:
                break
            ancestor = ancestor.parent
    return value


class Paths:
    """Resolve a narrow set of AST Path expressions without evaluating code."""
    def __init__(self, source: Path):
        self.values = {'__file__': source}

    def resolve(self, node):
        if isinstance(node, ast.Name):
            return self.values.get(node.id)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if len(value) < 500 and not any(char in value for char in '\r\n\x00') and '://' not in value and not value.startswith('~'):
                return value
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            if node.attr == 'parent' and isinstance(base, Path):
                return base.parent
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == 'parents':
            base = self.resolve(node.value.value)
            index = node.slice.value if isinstance(node.slice, ast.Constant) else None
            if isinstance(base, Path) and isinstance(index, int) and 0 <= index < min(6, len(base.parents)):
                return base.parents[index]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = self.resolve(node.left), self.resolve(node.right)
            if isinstance(left, Path) and isinstance(right, str):
                return left / right
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == 'Path' and len(node.args) == 1:
                value = self.resolve(node.args[0])
                if isinstance(value, (str, Path)):
                    return Path(value)
            if isinstance(node.func, ast.Attribute) and node.func.attr in {'resolve', 'absolute'} and not node.args:
                value = self.resolve(node.func.value)
                if isinstance(value, Path):
                    # Lexical normalization only; never execute a private helper.
                    return Path(os.path.abspath(value))
        return None


def inspect_private() -> dict:
    source = COLLECTOR / 'src/health_news_generator.py'
    info = source.lstat()
    if source.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
        raise ValueError('Private AST inspection is restricted to one bounded regular module')
    tree = ast.parse(source.read_bytes(), filename='private_health_generator')
    paths = Paths(source)
    for _ in range(3):
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = paths.resolve(node.value)
                for target in targets:
                    string_path = isinstance(value, str) and (value.startswith(('/', './', '../')) or
                                  ('/' in value and bool(re.search(r'\.(json|jsonl|csv|md|txt|log|db)$', value))))
                    if isinstance(target, ast.Name) and (isinstance(value, Path) or string_path):
                        paths.values[target.id] = value
    candidates, flags, env = [], set(), set()
    for name, value in paths.values.items():
        if name != '__file__' and isinstance(value, (str, Path)):
            candidates.append((Path(value), 'path_constant', name))
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == 'environ' and isinstance(node.value.value, ast.Name) and node.value.value.id == 'os':
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) and re.fullmatch(r'[A-Z_][A-Z0-9_]{0,79}', node.slice.value):
                env.add(node.slice.value)
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else (node.func.id if isinstance(node.func, ast.Name) else '')
        if name == 'add_argument':
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str) and re.fullmatch(r'--[a-z0-9-]{1,60}', argument.value):
                    flags.add(argument.value)
        environment_call = (name == 'getenv' and (isinstance(node.func, ast.Name) or
                            (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'os')))
        environment_call = environment_call or (name == 'get' and isinstance(node.func, ast.Attribute) and
                            isinstance(node.func.value, ast.Attribute) and node.func.value.attr == 'environ' and
                            isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == 'os')
        if environment_call and node.args and isinstance(node.args[0], ast.Constant):
            key = node.args[0].value
            if isinstance(key, str) and re.fullmatch(r'[A-Z_][A-Z0-9_]{0,79}', key):
                env.add(key)  # Names only; defaults and environment values omitted.
        target = paths.resolve(node.func.value) if isinstance(node.func, ast.Attribute) else None
        symbol = node.func.value.id if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) else ''
        if isinstance(target, Path) and name in {'read_text', 'read_bytes', 'open', 'glob', 'rglob', 'mkdir', 'write_text', 'write_bytes'}:
            role = 'write' if name in {'mkdir', 'write_text', 'write_bytes'} else 'read'
            if name in {'glob', 'rglob'} and node.args:
                glob = paths.resolve(node.args[0])
                if isinstance(glob, str):
                    target, role = target / glob, 'source_glob'
            candidates.append((target, role, symbol))
        if name in {'open', 'FileHandler'} and node.args:
            target = paths.resolve(node.args[0])
            if isinstance(target, (str, Path)):
                if isinstance(target, str) and not (target.startswith(('/', './', '../')) or re.search(r'\.(json|jsonl|csv|md|txt|log|db)$', target)):
                    continue
                mode = paths.resolve(node.args[1]) if len(node.args) > 1 else 'r'
                role = 'write' if name == 'FileHandler' or (isinstance(mode, str) and any(char in mode for char in 'wax+')) else 'read'
                candidates.append((Path(target), role, ''))
        for keyword in node.keywords:
            if keyword.arg == 'filename' and name in {'basicConfig', 'FileHandler'}:
                target = paths.resolve(keyword.value)
                if isinstance(target, (str, Path)):
                    candidates.append((Path(target), 'write', ''))
    rows, seen = [], set()
    for path, role, symbol in candidates:
        key = (str(path), role, symbol)
        if key not in seen:
            seen.add(key)
            rows.append(path_metadata(path, role, symbol))
        if len(rows) >= 128:
            break
    blockers = [row for row in rows if
                (row['role'] in {'read', 'source_glob'} and row.get('exists') and not row.get('readable')) or
                (row['role'] == 'write' and ((row.get('exists') and not row.get('writable')) or
                                            (not row.get('exists') and row.get('parent_writable') is False)))]
    return {'phase': 'static_private_metadata', 'source_module_count': 1, 'source_text_exported': False,
            'source_hash_exported': False, 'environment_values_exported': False,
            'cli_flags': sorted(flags), 'environment_names': sorted(env), 'io_metadata': rows,
            'possible_io_blockers': blockers, 'private_module_executed': False}


def as_bytes(value) -> bytes:
    return value if isinstance(value, bytes) else (value or '').encode('utf-8', errors='replace')


def output_summary(stdout: bytes, stderr: bytes) -> dict:
    text = (stdout + b'\n' + stderr).decode('utf-8', errors='replace')
    errors = sorted(name for name in ERRORS if re.search(r'\b' + name + r'\b', text))
    locations = []
    for match in re.finditer(r'''["'](/[^"'\r\n]{1,500})["']''', text):
        value = public_path(Path(os.path.normpath(match.group(1))))
        if value not in locations:
            locations.append(value)
        if len(locations) >= 12:
            break
    return {'error_categories': errors or ['unclassified_process_failure'], 'io_locations': locations,
            'errno_codes': sorted(set(int(value) for value in re.findall(r'\[Errno (\d{1,3})\]', text))),
            'stdout_bytes_retained': len(stdout), 'stderr_bytes_retained': len(stderr), 'raw_output_exported': False}


class NoDelivery:
    def __getattr__(self, name):
        raise RuntimeError('Diagnostic entry forbids notification and article ingestion')


def retry_prepare(args, directory: Path, inspection: dict) -> tuple[dict, int]:
    if args.run_id != RUN_ID:
        raise ValueError('Must use the existing isolated run ID')
    if inspection['possible_io_blockers'] and not args.allow_io_blockers:
        return {'state': 'io_gate_blocked', 'possible_io_blockers': inspection['possible_io_blockers'],
                'long_collection_started': False}, 4
    if not args.allow_collection:
        return {'state': 'collection_not_requested', 'long_collection_started': False}, 3
    runtime = STAGE / 'runtime'
    for path in [runtime, STAGE / 'state', STAGE / 'config.env', runtime / 'app']:
        if path.is_symlink() or not path.resolve().is_relative_to(STAGE):
            raise ValueError('Diagnostic runtime/state/config escaped the stage')
    worker_source = runtime / 'app/health_delivery.py'
    if hashlib.sha256(worker_source.read_bytes()).hexdigest() != WORKER_SHA:
        raise ValueError('Unexpected public Worker version')
    sys.path.insert(0, str(runtime))
    module = importlib.import_module('app.health_delivery')
    if Path(module.__file__).resolve() != worker_source.resolve():
        raise ValueError('Public Worker import escaped the isolated runtime')
    current = datetime.fromisoformat(args.at) if args.at else None
    config = module.Config.load(STAGE / 'config.env')
    worker = module.Worker(STAGE / 'state', module.Collector(COLLECTOR, config), NoDelivery(),
                           at=current, test_run=True, run_id=args.run_id, start_date=config.start_date)
    if worker.snapshot.exists():
        raise ValueError('Snapshot already exists; do not turn a replay into a diagnostic rerun')
    previous = module.subprocess.run
    failures = []
    calls = []

    def captured(command, *positional, **keywords):
        # Only the two known public adapter subprocess shapes are instrumented.
        expected_generator = isinstance(command, list) and len(command) >= 3 and command[1:3] == ['-m', 'src.health_news_generator']
        expected_adapter = isinstance(command, list) and len(command) >= 3 and command[1] == '-c' and command[2] == module.COLLECTOR_ADAPTER
        if not (expected_generator or expected_adapter) or Path(keywords.get('cwd', '.')).resolve() != COLLECTOR:
            raise ValueError('Unexpected subprocess shape in prepare-only diagnostic')
        phase = 'generator' if expected_generator else 'adapter'
        try:
            result = previous(command, *positional, **keywords)
        except subprocess.TimeoutExpired as error:
            calls.append({'phase': phase, 'exception_type': 'TimeoutExpired'})
            out, err = as_bytes(error.stdout), as_bytes(error.stderr)
            identity = phase + '-' + str(len(failures) + 1)
            private_file(directory / (identity + '.stdout.private.log'), out)
            private_file(directory / (identity + '.stderr.private.log'), err)
            failures.append({'phase': phase, 'returncode': None, 'exception_type': 'TimeoutExpired',
                             **output_summary(out, err)})
            raise
        except OSError as error:
            calls.append({'phase': phase, 'exception_type': type(error).__name__ if type(error).__name__ in ERRORS else 'OSError'})
            out, err = b'', str(error).encode('utf-8', errors='replace')
            identity = phase + '-' + str(len(failures) + 1)
            private_file(directory / (identity + '.stdout.private.log'), out)
            private_file(directory / (identity + '.stderr.private.log'), err)
            failures.append({'phase': phase, 'returncode': None, 'exception_type': type(error).__name__ if type(error).__name__ in ERRORS else 'OSError',
                             **output_summary(out, err)})
            raise
        calls.append({'phase': phase, 'returncode': result.returncode,
                      'stdout_bytes': len(as_bytes(result.stdout)), 'stderr_bytes': len(as_bytes(result.stderr))})
        if result.returncode:
            out, err = as_bytes(result.stdout), as_bytes(result.stderr)
            identity = phase + '-' + str(len(failures) + 1)
            private_file(directory / (identity + '.stdout.private.log'), out)
            private_file(directory / (identity + '.stderr.private.log'), err)
            failures.append({'phase': phase, 'returncode': result.returncode, **output_summary(out, err)})
        return result

    module.subprocess.run = captured
    try:
        result = worker.prepare()
        return {'state': 'prepare_finished', 'worker_state': result.get('state'),
                'article_count': result.get('articles'), 'batch_count': result.get('batches'),
                'long_collection_started': True, 'subprocess_calls': calls, 'failures': failures}, 0
    except Exception as error:
        kind = type(error).__name__
        return {'state': 'prepare_failed', 'error_category': kind if kind in ERRORS else 'unclassified_error',
                'long_collection_started': True, 'subprocess_calls': calls, 'failures': failures,
                'expected_markdown': path_metadata(STAGE / 'state' / 'collected' / ('test-' + args.run_id) / (worker.day + '.md'), 'read')}, 1
    finally:
        module.subprocess.run = previous


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['inspect', 'prepare'], default='inspect')
    parser.add_argument('--run-id', default=RUN_ID)
    parser.add_argument('--at', help='Optional explicit offset-aware test clock')
    parser.add_argument('--allow-collection', action='store_true', help='Explicitly permit the potentially long isolated prepare')
    parser.add_argument('--allow-io-blockers', action='store_true', help='Only after the operator reviews static optional-path false positives')
    args = parser.parse_args(argv)
    try:
        identity_guard()
        os.umask(0o077)
        directory = private_directory()
        # Suppress even unexpected private parser/import diagnostics. Only the
        # final allowlisted JSON below is printed; no traceback or raw strings.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            inspection = inspect_private()
            if args.mode == 'prepare':
                outcome, code = retry_prepare(args, directory, inspection)
            else:
                outcome, code = {'state': 'inspection_finished', 'long_collection_started': False}, 0
        value = {'status': 'DIAGNOSTIC_ONLY_NOT_ACCEPTANCE', 'stage': str(STAGE), 'diagnostic_directory': str(directory),
                 'uid': os.geteuid(), 'private_source_exported': False, 'private_logs_exported': False,
                 'messages_sent': 0, 'business_database_written': False, 'product_worker_modified': False,
                 'collector_permissions_modified': False, 'inspection': inspection, 'outcome': outcome}
        private_file(directory / 'metadata-summary.json', json.dumps(value, ensure_ascii=True, indent=2).encode('utf-8'))
        print(json.dumps(value, ensure_ascii=True))
        return code
    except Exception as error:
        kind = type(error).__name__
        print(json.dumps({'status': 'DIAGNOSTIC_ONLY_NOT_ACCEPTANCE', 'state': 'diagnostic_guard_or_inspection_failed',
                          'error_category': kind if kind in ERRORS else 'unclassified_error', 'raw_output_exported': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
