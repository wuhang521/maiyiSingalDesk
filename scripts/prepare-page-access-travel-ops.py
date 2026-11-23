"""Refresh guarded local program update evidence for the new approved URL."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import sys

ROOT = Path('D:/maiyi/SignalDesk')
OPS = Path('D:/maiyi/docs/delivery/desktop-account-permissions-20261110/activation-travel-20261111')
OLD = 'task-page-access-20261110'
NEW = 'task-page-access-travel-20261111'

def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()

def main():
    binding = ROOT / 'artifacts' / NEW / 'package-source-binding.json'
    assert json.loads(binding.read_text())['passed']
    for name in ('compare-program-payloads.py', 'prepare-local-program-update.ps1'):
        file = OPS / name
        value = file.read_text('utf-8-sig')
        assert OLD in value
        file.write_text(value.replace(OLD, NEW), 'utf-8')
    shutil.copyfile(binding, OPS / 'package-source-binding.json')
    result = subprocess.run([sys.executable, str(OPS / 'compare-program-payloads.py')], capture_output=True, text=True)
    (OPS / 'program-diff.log').write_text(result.stdout + result.stderr, 'utf-8')
    if result.returncode:
        raise RuntimeError('Program inventory failed; see local evidence')
    diff = OPS / 'program-installed-to-page-access-diff.json'
    rows = json.loads(diff.read_text('utf-8'))
    assert rows['changed_count'] == 19 and rows['added_count'] == 6
    for name in ('prepare-local-program-update.ps1', 'rollback-local-program-update.ps1'):
        file = OPS / name
        value = file.read_text('utf-8-sig')
        value, count = re.subn(r"\$expectedDiffSha256='[a-f0-9]{64}'", "$expectedDiffSha256='" + sha(diff) + "'", value)
        assert count == 1
        if name.startswith('prepare'):
            value, count = re.subn(r"\$expectedBindingSha256='[a-f0-9]{64}'", "$expectedBindingSha256='" + sha(OPS / 'package-source-binding.json') + "'", value)
            assert count == 1
        file.write_text(value, 'utf-8-sig')
    print(json.dumps({'passed': True, 'changed': 19, 'added': 6, 'diff_sha256': sha(diff), 'binding_sha256': sha(OPS / 'package-source-binding.json'), 'installed': False}))

if __name__ == '__main__':
    main()
