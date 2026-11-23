"""Stage/apply only the exact reviewed public permission package on the Task host."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path('D:/maiyi/docs/delivery/desktop-account-permissions-20261110/evidence/page-backend-20261111')
STAGE = '/var/tmp/signaldesk-page-task-access-20261111'
HOST = 'hermes-desktop-vps'
DEPLOYER = ROOT / 'server/scripts/deploy_page_task_access.py'

def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()

def command(args, timeout=120):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        print(json.dumps({'passed': False, 'action': Path(args[0]).name, 'exit_code': result.returncode,
            'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr), 'private_output_exported': False}))
        if args[0] == 'ssh':
            try:
                print(json.dumps(json.loads(result.stdout)))
            except (ValueError, TypeError):
                pass
        raise SystemExit(result.returncode)
    return result.stdout

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('stage', 'apply'))
    args = parser.parse_args()
    manifest = EVIDENCE / 'manifest.json'
    bundle = EVIDENCE / 'page-task-backend-preview.tar.gz'
    record = json.loads(manifest.read_text('utf-8'))
    assert sha(bundle) == record['archive']['sha256'] == '88854f49980cf7e2369fe08529525d29d0e8f38773df32f92f424b697d0caaef'
    compile(DEPLOYER.read_bytes(), str(DEPLOYER), 'exec')
    binding = {'manifest_sha256': sha(manifest), 'deployment_sha256': sha(DEPLOYER), 'bundle_sha256': sha(bundle)}
    if args.mode == 'stage':
        command(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', HOST,
            'test ! -e ' + STAGE + ' && install -d -m 700 ' + STAGE])
        command(['scp', '-q', '-o', 'BatchMode=yes', str(bundle), str(manifest), str(DEPLOYER), HOST + ':' + STAGE + '/'])
    else:
        staged = json.loads((EVIDENCE / 'production-staged.json').read_text())
        assert staged['binding'] == binding and staged['result']['passed']
    mode = 'prepare' if args.mode == 'stage' else 'apply'
    guard = "import hashlib,runpy,sys; p='" + STAGE + "/deploy_page_task_access.py'; assert hashlib.sha256(open(p,'rb').read()).hexdigest()=='" + binding['deployment_sha256'] + "'; sys.argv=[p,'" + mode + "','--manifest-sha256','" + binding['manifest_sha256'] + "']; runpy.run_path(p,run_name='__main__')"
    # No user input or secret is interpolated. Python repr is quoted with POSIX
    # shell quoting, independently of JSON encoding.
    quoted_guard = "'" + guard.replace("'", "'\"'\"'") + "'"
    result = json.loads(command(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', HOST,
        'cd /opt/signaldesk-task && .venv/bin/python -c ' + quoted_guard], timeout=240))
    receipt = {'binding': binding, 'result': result, 'private_source_or_credentials_exported': False}
    output = EVIDENCE / ('production-staged.json' if args.mode == 'stage' else 'production-deployed.json')
    output.write_text(json.dumps(receipt, indent=2) + '\n', 'utf-8')
    print(json.dumps(receipt))

if __name__ == '__main__':
    main()
