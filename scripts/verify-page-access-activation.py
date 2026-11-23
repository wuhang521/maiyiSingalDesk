"""Read-only installed-byte and server-metadata verification; no personal data reads."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess

DELIVERY = Path('D:/maiyi/docs/delivery/desktop-account-permissions-20261110')
OPS = DELIVERY / 'activation-travel-20261111'
ROOT = Path('D:/maiyi/SignalDesk')

def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()

def main():
    applied = json.loads((OPS / 'local-program-update.applied.json').read_text('utf-8-sig'))
    assert applied['applied'] and applied['mode'] == 'applied'
    assert len(applied['changes']) == 25
    for item in applied['changes']:
        target = Path(item['target'])
        assert target.is_relative_to(Path('D:/maiyi/signaldesk-desktop')) and not target.is_symlink()
        assert sha(target) == item['new_sha256']
    probe = (DELIVERY / 'evidence/production-readonly-probe.py').read_text('utf-8')
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', 'hermes-desktop-vps',
        'cd /opt/signaldesk-task && .venv/bin/python -'], input=probe, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError('remote_metadata_verification_failed')
    report = json.loads(result.stdout)
    expected = json.loads((DELIVERY / 'evidence/page-backend-20261111/manifest.json').read_text())
    for row in expected['files']:
        assert report['files'][row['target']]['sha256'] == row['after_sha256']
    assert report['files']['app/main.py']['sha256'] == '0ad5b9d0c01b5777f918d8310cb1b87de065d9cfd4c4d59e78728ebde7ff97e3'
    assert report['database_heads'] == ['20261008_12']
    columns = [c for c in report['user_account_columns'] if c['name'] == 'allowed_pages']
    assert len(columns) == 1 and columns[0]['type'] == 'JSON' and columns[0]['nullable']
    (OPS / 'production-after-readonly.json').write_text(json.dumps(report, indent=2), 'utf-8')
    record = {'passed': True, 'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'installed_members_verified': 25,
        'production_modules_verified': 7, 'database_head_preserved': True, 'nullable_page_column_verified': True,
        'production_counts': report['row_counts'], 'read_only': True, 'personal_profile_read': False,
        'installed_asar_sha256': sha(Path('D:/maiyi/signaldesk-desktop/resources/app.asar')),
        'installed_executable_sha256': sha(Path('D:/maiyi/signaldesk-desktop/迈医协同.exe'))}
    (OPS / 'activation-verification.json').write_text(json.dumps(record, indent=2), 'utf-8')
    print(json.dumps(record))

if __name__ == '__main__':
    main()
