"""Bind actual installation, production deployment, test and user acceptance evidence."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path('D:/maiyi/SignalDesk')
DELIVERY = Path('D:/maiyi/docs/delivery/desktop-account-permissions-20261110')
OPS = DELIVERY / 'activation-travel-20261111'

def load(file):
    return json.loads(file.read_text('utf-8-sig'))

def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()

def main():
    binding = load(OPS / 'package-source-binding.json')
    installed = load(OPS / 'activation-verification.json')
    applied = load(OPS / 'local-program-update.applied.json')
    user = load(OPS / 'user-desktop-confirmation.json')
    deployed = load(DELIVERY / 'evidence/page-backend-20261111/production-deployed.json')
    page_run = ROOT / 'artifacts/members/page-access-run-LyW1Fw'
    e2e = load(page_run / 'result.json')
    office = load(ROOT / 'artifacts/task-page-access-travel-20261111/office-native/run-k6B4VP/results.json')
    xml = ET.parse(ROOT / 'artifacts/task-page-access-travel-20261111/backend.xml').getroot()
    testcases = list(xml.iter('testcase'))
    assert len(testcases) == 32 and not list(xml.iter('failure')) and not list(xml.iter('error'))
    assert e2e['passed'] and e2e['unchangedFiles'] and len(e2e['steps']) == 5 and len(e2e['apiChecks']) == 15
    assert (page_run / '02-admin-edits-member-grants.png').is_file()
    assert office['status'] == 'PASS' and office['counts']['pass'] == 7 and office['counts']['fail'] == 0
    assert all(user[key] for key in ('client_main_window_visible', 'member_page_access_control_visible'))
    assert user['external_destination_confirmed_by_user'] == 'https://maiyitechmed.com/travel/'
    assert binding['passed'] and installed['passed'] and applied['applied'] and deployed['result']['passed']
    assert sha(Path('D:/maiyi/signaldesk-desktop/resources/app.asar')) == binding['asar_sha256'] == installed['installed_asar_sha256']
    for name, expected in e2e['loadedFiles'].items():
        assert sha(ROOT / name) == expected
    assert binding['runtime_manifest_files'] == 5147
    assert deployed['result']['historical_tables_preserved'] == 37
    assert load(OPS / 'program-rollback.plan.json')['mode'] == 'rollback_dry_run'
    current = {'passed': True, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'installed_and_backend_updated': True, 'desktop_acceptance_by_user': True,
        'frontend_typecheck_build_and_204_tests': 'PASS_OBSERVED_CURRENT_TURN_COMMAND_OUTPUT',
        'task_member_api_cases': 32, 'production_compatibility_cases': 46,
        'electron_scenarios': 5, 'electron_api_assertions': 15, 'packaged_link_checks': 7,
        'historical_tables_verified_unchanged_during_schema_upgrade': 37,
        'personal_state_snapshot': 'opaque_in_original_profile',
        'real_member_grants_changed_for_test': False, 'real_dual_account_login_test': 'NOT_RUN',
        'qq_test': 'EXCLUDED_BY_USER', 'new_setup_exe': 'NOT_BUILT', 'git_commit_or_push': False}
    (OPS / 'final-verification.json').write_text(json.dumps(current, indent=2) + '\n', 'utf-8')
    evidence = [p for p in OPS.iterdir() if p.is_file() and p.name != 'artifact-manifest.json']
    evidence.extend([ROOT / p for p in (
        'src/components/Sidebar.tsx', 'scripts/build-page-access-travel.py',
        'scripts/office-entry-e2e.mjs', 'scripts/page-access-remote-preview.py',
        'scripts/page-access-remote-deploy.py', 'server/scripts/deploy_page_task_access.py',
        'scripts/verify-page-access-activation.py', 'scripts/finalize-page-access-travel.py',
        'artifacts/task-page-access-travel-20261111/backend.xml',
        'artifacts/members/page-access-run-LyW1Fw/result.json',
        'artifacts/task-page-access-travel-20261111/office-native/run-k6B4VP/results.json')])
    evidence.extend([DELIVERY / 'evidence/page-backend-20261111' / name for name in
        ('manifest.json', 'production-main-preview.json', 'production-staged.json', 'production-deployed.json', 'compatibility-api.final.log', 'schema-compatibility.json')])
    manifest = {'checked_at_utc': current['checked_at_utc'], 'status': 'CURRENT_CLIENT_AND_BACKEND_UPDATED_USER_CONFIRMED',
        'files': [{'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in sorted(set(evidence))]}
    (OPS / 'artifact-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    assert all(sha(Path(item['path'])) == item['sha256'] for item in manifest['files'])
    print(json.dumps({**current, 'artifact_files_verified': len(manifest['files'])}))

if __name__ == '__main__':
    main()
