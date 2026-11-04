"""Plan reviewed source/evidence archives; dry-run is the default, never publish.

Only --write --plan <frozen-plan.json> creates ZIPs, with exclusive new filenames.
No product, UI, server, credentials file, profile, or database is executed/read.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DELIVERY = Path('D:/maiyi/docs/delivery/health-channel-push-20261030')
BASELINE = Path('D:/maiyi/docs/delivery/knowledge-remaining-acceptance-20261028/packages/SignalDesk-0.6.3-source.zip')
BASELINE_SHA = '5499cffde192181611f51e0862992a8ea9a7a1374cba7c56581a35ace8868486'
BASELINE_AUDIT = BASELINE.parents[1]/'source-archive-audit.json'
VERSION = '0.6.4'
DENY_DIRS = {'.git','node_modules','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.vite',
    '.venv','.venv-signaldesk','release','dist','coverage','artifacts','test-results','playwright-report',
    'private','privateconfigs','private-configs','profiles','profile','cache','caches','code cache','gpucache',
    'default','session storage','local storage','indexeddb','crashpad','logs','data','files','sandbox-output',
    'stage','stages','staging','diagnostics','snapshots','collected','worker-state','raw','raw-data','rawdata'}
SOURCE_DENY_DIRS = DENY_DIRS-{'data','files','default'}
DENY_NAMES = {'credentials.json','auth-session.json','local-login.json','video-config.json','private-config.json',
    'config.json','running.json','cookies','local state','login data','web data'}
DENY_EXT = {'.db','.sqlite','.sqlite3','.dpapi','.key','.pem','.p12','.pfx','.pyc','.pyo','.exe','.dll',
    '.zip','.gz','.tar','.7z','.mp3','.mp4','.wav','.webm','.flac','.mov','.m4a'}
NEW_CODE = {'.py','.pyi','.ts','.tsx','.js','.jsx','.cjs','.mjs','.css','.scss','.html','.svg','.mako'}
NEW_TREES = ('src','electron','server/app','server/migrations','server/tests')
EVIDENCE_TREES = {'frontend','backend','scheduler','installer','evidence'}
EVIDENCE_EXT = {'.json','.log','.png','.md'}
EXPLICIT_SOURCE = {
    'server/scripts/test_health_channels_mysql.py',
    'server/HEALTH_COLLECTOR.md',
    *(f'server/deploy/signaldesk-health-{mode}.{kind}' for mode in ('prepare','deliver') for kind in ('service','timer')),
}
PUBLIC_HEALTH_SCRIPTS = {
    'scripts/'+name for name in (
        'activate-health-schedule.py','test_activate_health_schedule.py',
        'apply-health-collector-update.py','test_apply_health_collector_update.py',
        'apply-health-ack-update.py','test_apply_health_ack_update.py','check-health-preserved-data.py',
        'build-health-backend.py','build-health-mysql-source.py','build-health-collector-update.py','check-health-private-mysql.py',
        'check-health-service-runtime.py','deploy-health-channels.py','diagnose-health-collector.py',
        'health-channel-e2e.mjs','health-content-contract.cjs','inspect-health-scheduler-guard.py',
        'install-health-preflight-unit.py','install-health-timer-acceptance.py','test_install_health_timer_acceptance.py',
        'prepare-health-release.py','test_prepare_health_release.py','probe-health-collector-network.py',
        'probe-health-live.py','probe-health-ntfy.cjs','repair-health-config-parent.py',
        'probe-health-activation-preflight.py',
        'resume-health-collector-acceptance.py','run-health-rss-preflight.py','start-health-runtime-acceptance.py',
        'test-health-stream-status.cjs','test-health-window-lifecycle.cjs','verify-health-server-package.py',
        'test_verify_health_server_package.py')
}
FROZEN_CRITICAL_SHA256 = {
    'electron/main.cjs':'9b0aaa011d79cb78ae5b7d27c88405601c8e5a9db5a34c8a9f394dc6c25ce046',
    'scripts/health-channel-e2e.mjs':'836ae3196ed7eb5b1c8a23b7941862f043286142cb82071faae4af6aa5dc8360',
    'scripts/test-health-window-lifecycle.cjs':'cc51476cc2cdcb8756654ffcb2e0a898374bb8f74f9bddeb885933b77a70d4f7',
    'scripts/activate-health-schedule.py':'7d6c97a200b75650a31ea5fab7b426f8c9a071a3058f1e80fcafcc0278717a54',
    'scripts/test_activate_health_schedule.py':'cc0d264f4070dd0459e94e5655c2648829a1fc1e0f7d4a8b9a7db58a82e7b8d8',
    'server/app/health_delivery.py':'91e5f649ff6773b08b0ba40e92656ed5457b42c811ac873c2a1d3302f1a3130d',
    'server/app/health_collector.py':'04e179b61df6d86e3c714c49fca297e9517fc01b8df674c083743d0fa87cf5be',
    'server/tests/test_health_collector.py':'34b843874c6b6b8ca5b4fe76044f12f74eb353eda330bea04b140c8cb14233ed',
    'scripts/install-health-timer-acceptance.py':'8f1187530a6825be99f804091889e25498d0539b9d56e9cc46fca6c16aad4074',
    'scripts/apply-health-ack-update.py':'3288400e7e171ca63ba0a2bdd12a4371acbda7e3c3ff2f6419da330693789e2d',
    'scripts/test_apply_health_ack_update.py':'acc02655143d1d233ca2f88dc8f7a3d084c25d2c396fef0d8c2a4c93d01fe825',
    'scripts/run-health-rss-preflight.py':'9ffcde279cc3823b86919abb7f087777f4d5edb2f57f4538dee60965fa08295e',
    'server/HEALTH_COLLECTOR.md':'21a8f1af7923dbf3989df7903df49d2a837e3f1e553a3cf2af7ec1e93bd39bd7',
    'scripts/check-health-preserved-data.py':'5864465313e873ae4f566cba4bcb6fd0b3b7119db2200de5a7e3ed74cd36259a',
    'server/tests/test_health_preserved_data_probe.py':'8381b7b00781451ede2c83df75a276886b9b715739250631b6119d95adfd6d45',
}
# Operational code bytes are reviewed, while Root still owns final activation,
# R3 receipt and document approval. The write guard below remains enabled.
PUBLIC_GUIDES = {
    'release-guides/OPERATIONS-HELPERS.md':'packaging/OPERATIONS-HELPERS.md',
    'release-guides/activation/README.md':'scheduler/activation/README.md',
    'release-guides/activation/synthetic-tests.log':'scheduler/activation/synthetic-tests.log',
    'release-guides/activation/manifest.json':'scheduler/activation/manifest.json',
    'release-guides/ack-update/README.md':'scheduler/ack-update/README.md',
    'release-guides/ack-update/synthetic-tests.log':'scheduler/ack-update/synthetic-tests.log',
    'release-guides/ack-update/manifest.json':'scheduler/ack-update/manifest.json',
}
CRITICAL = {'src/App.tsx','src/types.ts','src/lib/channelIdentity.ts','src/lib/storage.ts','src/lib/articleApi.ts',
    'src/lib/ntfy.ts','src/components/ChannelDialog.tsx','src/components/ChannelsView.tsx','electron/main.cjs',
    'src/HealthChannelCompatibility.test.tsx','src/lib/channelIdentity.test.ts','scripts/health-channel-e2e.mjs',
    'scripts/test-health-stream-status.cjs','scripts/test-health-window-lifecycle.cjs','scripts/health-content-contract.cjs','server/tests/health_channel_preview.py',
    'server/app/articles.py','server/app/schemas.py','server/app/channel_aliases.py','server/app/channel_reconcile.py',
    'server/app/health_relay.py','server/app/health_delivery.py','server/app/health_collector.py',
    'server/tests/test_health_collector.py',*EXPLICIT_SOURCE,*PUBLIC_HEALTH_SCRIPTS,*PUBLIC_GUIDES,*FROZEN_CRITICAL_SHA256}
EXTRA_SOURCE = {'delivery-tests/deployment-parent-permissions.py':
    (DELIVERY/'backend/deployment-parent-permissions.py','702f64a3442c441676e1797625761cd3d886fb091414363a327c0519745e2590')}
SAFE_PROGRAM_RESULTS = {
    'evidence/channel-e2e/result.before-window-fix.json':'6b1047e6c9594aca5d0de76baa0053e2c3bd9d5dd68b9bd3cd1c6584000aaa9d',
    'evidence/channel-e2e/result.json':'7dbdb2b4e62606718a193002cb85efa50d62229c630642426ab38649296037e9',
    'evidence/background-r3/channel-e2e/result.json':'66dc3c65883a06d30039040546d1d752a4206041b2d5a2c18a929cd9e1b1d733',
}
SOURCE_REVIEW = DELIVERY/'packaging/reviewed-source-lines.json'
IMAGE_REVIEW = DELIVERY/'packaging/reviewed-evidence-images.json'
PUBLIC_EVIDENCE_MD = {
    'README.md','delivery.md','test-report.md','traceability.md','prototype-notes.md','implementation.md','requirements.md',
    'requirements-acceptance.md','prototype-design.md','retrospective.md',
    'frontend/report.md','frontend/window-lifecycle-plan.md','backend/report.md','backend/deployment-review.md',
    'backend/health-relay-report.md','backend/content-contract.md','scheduler/README.md',
    'backend/health-collector-report.md','scheduler/rss-stage-notes.md',
    'backend/original-subset-probe-review.md','scheduler/ntfy-whitespace-primary-evidence.md',
    'backend/full-ack-final.md','backend/full-rss-final.md','scheduler/ack-update/README.md',
    'scheduler/systemd219-compatibility.md','scheduler/collector-diagnostic-prepared.md',
    'scheduler/official-rss-feasibility.md','scheduler/rss-adapter-feasibility.md','scheduler/worker-smallfix-report.md',
    'scheduler/activation/README.md','installer/README.md','installer/reopen-r2/README.md',
    'evidence/channel-e2e/report.md','evidence/channel-e2e/report.before-window-fix.md',
    'packaging/OPERATIONS-HELPERS.md',
    'frontend/background-r3/README.md',
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> str:
    p = PurePosixPath(name)
    if not name or name.startswith(('/','\\')) or '\\' in name or ':' in name or any(x in {'','.','..'} for x in name.split('/')):
        raise ValueError('Unsafe archive member')
    return p.as_posix()


def plain(path: Path) -> None:
    for p in (path.absolute(), *path.absolute().parents):
        if p.is_symlink() or getattr(p,'is_junction',lambda:False)():
            raise ValueError('Linked source/output path rejected')
        if p.exists() and getattr(p.stat(),'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
            raise ValueError('Reparse-point source/output path rejected')


def denied(name: str, *, template=False, source=False) -> bool:
    parts = safe_name(name).split('/'); base = parts[-1].lower()
    folders = [p.lower() for p in parts[:-1]]
    return (any(p in (SOURCE_DENY_DIRS if source else DENY_DIRS) or p.startswith(('run-','profile-','attempt-','program-run-','backend-run-','private-','stage-','staging-')) for p in folders)
        or base in DENY_NAMES or (base.startswith('.env') or base.endswith('.env')) and not (template and base=='.env.example')
        or bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}\.(?:md|html|pdf)',base))
        or not source and bool(re.search(r'(?:^|[._-])(?:raw|stdout|stderr)(?:[._-]|$)',base))
        or Path(base).suffix in DENY_EXT)


def walk(root: Path, relative: str, *, source=False):
    base=root/relative; plain(base)
    if not base.is_dir(): return
    for folder, subdirs, names in os.walk(base,followlinks=False):
        current=Path(folder)
        for name in list(subdirs):
            rel=(current/name).relative_to(root).as_posix()
            if denied(rel+'/placeholder.txt',source=source):
                subdirs.remove(name); continue
            plain(current/name)
        for name in names:
            path=current/name; rel=path.relative_to(root).as_posix()
            if not denied(rel,template=True,source=source):
                plain(path)
                if path.is_file(): yield rel,path


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def legacy_scanner(root: Path, baseline_files: dict):
    relative='scripts/prepare-public-release.py'; path=root/relative
    data=path.read_bytes()
    if sha(data)!=baseline_files[relative]['sha256']:
        raise ValueError('Legacy privacy scanner must match the audited 0.6.3 byte hash')
    scope={'__name__':'reviewed_source_scanner','__file__':str(path)}
    exec(compile(data,str(path),'exec'),scope)
    return scope


def reviewed_source_lines():
    audit=load_json(BASELINE_AUDIT)
    if not audit['passed'] or audit['archive_sha256']!=BASELINE_SHA: raise ValueError('Baseline audit mismatch')
    rows=audit['privacy']['findings_redacted']
    if SOURCE_REVIEW.exists(): rows+=load_json(SOURCE_REVIEW)['lines']
    return {(r['file'],r['line_sha256'],r['rule']):r.get('classification','reviewed fixture') for r in rows}


def check_template(data: bytes) -> bool:
    for line in data.decode('utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line: continue
        key,value=line.split('=',1); value=value.strip().strip('\"\'')
        if not re.search(r'(KEY|TOKEN|PASS|SECRET|DATABASE_URL)',key,re.I): continue
        if not value or value.lower().startswith(('replace','your-','your_','<','${','sqlite:///')): continue
        if key.strip().upper().endswith('DATABASE_URL'):
            try:
                parsed=urlsplit(value)
                if parsed.scheme.startswith('sqlite') or parsed.password and parsed.password.lower().startswith(('replace','your-','your_','<')): continue
            except ValueError: pass
        return False
    return True


def verify_critical_hashes(payloads):
    for name,expected in FROZEN_CRITICAL_SHA256.items():
        if name not in payloads or sha(payloads[name])!=expected:
            raise ValueError('Root-reviewed critical source SHA differs: '+name)


def source_plan(root: Path):
    plain(root); plain(BASELINE)
    if sha(BASELINE.read_bytes())!=BASELINE_SHA: raise ValueError('Immutable baseline ZIP hash mismatch')
    with zipfile.ZipFile(BASELINE) as archive:
        baseline=json.loads(archive.read('SOURCE-MANIFEST.json'))
    old={safe_name(r['path']):r for r in baseline['files']}
    selected={name:root/name for name in old}
    for relative in NEW_TREES:
        for name,path in walk(root,relative,source=True):
            if path.suffix.lower() in NEW_CODE: selected[name]=path
    for name in PUBLIC_HEALTH_SCRIPTS:
        path=root/name; plain(path); selected[name]=path
    for name in EXPLICIT_SOURCE: selected[name]=root/name
    for path in root.glob('KNOWLEDGE-*.md'): selected[path.name]=path
    for name,(path,digest) in EXTRA_SOURCE.items():
        plain(path)
        if sha(path.read_bytes())!=digest: raise ValueError('Explicit external synthetic helper changed')
        selected[name]=path
    # These are reviewed public prose/metadata, not a recursive delivery import.
    # Scan their actual bytes below, just like repository source; never read a
    # server stage, raw output or runtime config to satisfy a missing guide.
    for name,relative in PUBLIC_GUIDES.items():
        path=DELIVERY/relative; plain(path); selected[name]=path
    for label,path in [('reviewed-source-lines.json',SOURCE_REVIEW),('reviewed-evidence-images.json',IMAGE_REVIEW)]:
        if path.exists(): selected['release-review/'+label]=path
    if not CRITICAL.issubset(selected): raise ValueError('Critical health source member missing')
    package=load_json(root/'package.json'); lock=load_json(root/'package-lock.json')
    if not package['version']==lock['version']==lock['packages']['']['version']==VERSION: raise ValueError('Package/lock version mismatch')
    scanner=legacy_scanner(root,old); reviewed=reviewed_source_lines()
    rows=[]; findings=[]; accepted=[]; payloads={}
    for path in (root/'scripts').iterdir():
        name=path.relative_to(root).as_posix()
        if path.is_file() and path.suffix.lower() in NEW_CODE and name not in selected and not denied(name,source=True):
            findings.append({'file':name,'rule':'new-script-outside-reviewed-health-whitelist'})
    for name,path in sorted(selected.items()):
        plain(path)
        if denied(name,template=True,source=True) or not path.is_file(): raise ValueError('Source whitelist contains forbidden or missing member: '+name)
        data=path.read_bytes(); payloads[name]=data
        row={'path':name,'bytes':len(data),'sha256':sha(data),'baseline_sha256':old.get(name,{}).get('sha256'),
             'new':name not in old,'external_reviewed_helper':name in EXTRA_SOURCE}
        rows.append(row)
        if path.name=='.env.example' and not check_template(data): findings.append({'file':name,'rule':'non-placeholder-env-template'})
        if path.suffix.lower() in {'.png','.webp','.jpg','.jpeg','.ico','.amr','.ttf','.otf'}:
            if name not in old or row['sha256']!=old[name]['sha256']: findings.append({'file':name,'rule':'unreviewed-new-binary'})
            continue
        try: text=data.decode('utf-8-sig')
        except UnicodeError:
            findings.append({'file':name,'rule':'unexpected-binary-source'}); continue
        lines=text.splitlines()
        for finding in scanner['scan_text'](name,text):
            number=finding['line']; digest=sha(lines[number-1].encode())
            item={**finding,'line_sha256':digest}
            classification=reviewed.get((name,digest,finding['rule']))
            if classification: accepted.append({**item,'classification':classification})
            else: findings.append(item)
    if len({r['path'].lower() for r in rows})!=len(rows): raise ValueError('Case-insensitive archive member collision')
    verify_critical_hashes(payloads)
    return {'version':VERSION,'baseline_sha256':BASELINE_SHA,'file_count':len(rows),'files':rows,
            'privacy':{'unreviewed':findings,'reviewed_findings':accepted},'critical_paths':sorted(CRITICAL),
            'root_reviewed_critical_sha256':FROZEN_CRITICAL_SHA256,'activation_source_freeze_pending':True},payloads


PROFILE=re.compile(r'(?i)C:(?:[/\\]|\\\\)Users(?:[/\\]|\\\\)(?!Public\b|Default\b|<)[A-Za-z0-9_.-]+')
BEARER=re.compile(r'(?i)(\bBearer\s+)[A-Za-z0-9._-]{8,}')
STRONG=re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{20,}|AIza[A-Za-z0-9_-]{35}|hf_[A-Za-z0-9]{30,}|eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,})\b')
ASSIGN=re.compile(r'''(?i)(\b(?:[a-z_]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret[_-]?key|authorization)[a-z_]*)["']?\s*[:=]\s*["'])([^"'\r\n]+)(["'])''')
SENSITIVE=re.compile(r'(?i)^(?:.*(?:api_?key|access_?token|auth_?token|password|passwd|secret_?key)|authorization|cookie|session_?id|session_?token|refresh_?token|token|secret|username|display_name|displayName|operatorName|operator)$')
SAFE_TOPICS={'health','demo-health','synthetic-health','health-e2e','health-topic','retired-health','retired-topic','custom-topic','<topic>','[redacted]'}
PAYLOAD_KEYS={'body','message','markdown','html','raw','raw_body','raw_message','localstorage','storagestate','cookies'}
PAYLOAD_KEYS |= {'stdout','stderr','raw_stdout','raw_stderr','payload'}


def redact_text(text: str):
    counts={}
    def apply(rule,pattern,replacement):
        nonlocal text
        text,count=pattern.subn(replacement,text)
        if count: counts[rule]=count
    apply('personal-profile-path',PROFILE,'C:/Users/<user>')
    apply('bearer-value',BEARER,r'\1[REDACTED]')
    apply('basic-authorization',re.compile(r'(?i)(\bBasic\s+)[A-Za-z0-9+/=]{8,}'),r'\1[REDACTED]')
    apply('url-credentials',re.compile(r'''(?i)([a-z][a-z0-9+.-]*://)[^/@\s"']+:[^@\s"']+@'''),r'\1[REDACTED-CREDENTIALS]@')
    apply('provider-token',STRONG,'[REDACTED]')
    apply('credential-assignment',ASSIGN,r'\1[REDACTED]\3')
    apply('ntfy-topic-url',re.compile(r'(https?://ntfy\.sh/)[A-Za-z0-9_-]{6,}'),r'\1[REDACTED-TOPIC]')
    apply('private-key-block',re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----',re.S),'[REDACTED PRIVATE KEY]')
    return text,counts


def evidence_payload(data: bytes, suffix: str):
    counts={}
    if data.startswith((b'\xff\xfe',b'\xfe\xff')):
        text=data.decode('utf-16'); counts['utf16-to-utf8']=1
    else:
        try: text=data.decode('utf-8-sig')
        except UnicodeError:
            if suffix!='.log': raise
            text=data.decode('cp936'); counts['cp936-to-utf8']=1
    if suffix=='.json':
        value=json.loads(text)
        def visit(item):
            if isinstance(item,list): return [visit(v) for v in item]
            if not isinstance(item,dict): return item
            result={}
            for key,value in item.items():
                if (key.lower() in PAYLOAD_KEYS or key.lower() in {'articles','feed','trash','saved','read'} and isinstance(value,(list,dict))) and value:
                    result[key]='[REDACTED PAYLOAD]'; counts['raw-payload-json-field']=counts.get('raw-payload-json-field',0)+1
                elif isinstance(value,str) and value and (SENSITIVE.fullmatch(key) or key.lower() in {'topic','ntfy_topic'} and value not in SAFE_TOPICS):
                    result[key]='[REDACTED]'; counts['sensitive-json-field']=counts.get('sensitive-json-field',0)+1
                else: result[key]=visit(value)
            return result
        value=visit(value)
        # Retain original JSON bytes when structural redaction was unnecessary.
        if counts: text=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    text,other=redact_text(text)
    for key,count in other.items(): counts[key]=counts.get(key,0)+count
    if suffix=='.json': json.loads(text)
    return text.encode('utf-8') if counts else data,counts


def image_approvals(delivery: Path):
    approved={}
    for name,digest in SAFE_PROGRAM_RESULTS.items():
        result=delivery/name
        if result.exists() and sha(result.read_bytes())==digest:
            run=load_json(result)['attempt']
            for path in (result.parent).glob(run+'-*.png'):
                approved[path.relative_to(delivery).as_posix()]={'sha256':sha(path.read_bytes()),'classification':'owned synthetic 0.6.4 Electron profile; no production data'}
    if IMAGE_REVIEW.exists():
        for row in load_json(IMAGE_REVIEW)['images']: approved[row['path']]=row
    return approved


def evidence_plan(delivery: Path):
    plain(delivery); approvals=image_approvals(delivery)
    selected={}; omitted=[]; omitted_text=[]; omitted_private=[]
    for path in delivery.iterdir():
        if path.is_file() and path.suffix.lower() in EVIDENCE_EXT and path.name not in {'delivery-manifest.json'}:
            if denied(path.name): omitted_private.append({'path':path.name,'reason':'Runtime/raw material excluded before content read'})
            else: selected[path.name]=path
    for tree in EVIDENCE_TREES:
        for name,path in walk(delivery,tree):
            if path.suffix.lower() in EVIDENCE_EXT: selected[name]=path
    for path in (delivery/'packages').glob('*.json'):
        name=path.relative_to(delivery).as_posix()
        if denied(name): omitted_private.append({'path':name,'reason':'Runtime/raw material excluded before content read'})
        else: selected[name]=path
    for name in ('packages/background-r3/public-artifacts.json',):
        path=delivery/name
        if path.is_file(): selected[name]=path
    self_test_log=delivery/'packaging/self-test.log'
    if self_test_log.is_file(): selected['packaging/self-test.log']=self_test_log
    for name in list(selected):
        if selected[name].suffix.lower()=='.md' and name not in PUBLIC_EVIDENCE_MD:
            omitted_text.append({'path':name,'reason':'Markdown is not an explicitly reviewed public guide/report; content was not read'})
            del selected[name]
    rows=[]; payloads={}; findings=[]
    for name,path in sorted(selected.items()):
        plain(path)
        if denied(name): raise ValueError('Forbidden evidence member')
        data=path.read_bytes()
        if path.suffix.lower()=='.png':
            approval=approvals.get(name)
            if not approval or approval['sha256']!=sha(data):
                omitted.append({'path':name,'reason':'PNG needs explicit content review and byte hash approval'}); continue
            payload,redactions=data,{}
        else:
            try: payload,redactions=evidence_payload(data,path.suffix.lower())
            except (UnicodeError,json.JSONDecodeError):
                findings.append({'file':name,'rule':'invalid-text-or-json'}); continue
        payloads[name]=payload
        rows.append({'path':name,'original_bytes':len(data),'original_sha256':sha(data),'bytes':len(payload),
                     'sha256':sha(payload),'redactions':redactions,'png_review':approvals.get(name)})
    return {'file_count':len(rows),'files':rows,'omitted_png':omitted,'omitted_unreviewed_markdown':omitted_text,
            'omitted_private_runtime_material':omitted_private,
            'release_roles':{'current_program':'installer/background-r3/artifact-manifest.json',
                'current_isolated_native_e2e':'evidence/background-r3/channel-e2e/result.json',
                'historical_program':'installer/artifact-manifest.json',
                'historical_reopen_r2_program':'installer/reopen-r2/artifact-manifest.json',
                'current_backend_manifest':'packages/backend-increment-rss-ack-manifest.json',
                'current_backend_regression':'backend/full-ack-final-summary.json',
                'historical_preack_backend_manifest':'packages/backend-increment-rss-manifest.json',
                'r1_and_red_failure_evidence_preserved':True},
            'privacy':{'unreviewed':findings},
            'extensions':sorted(EVIDENCE_EXT),'redaction_scope':'bounded mechanical credential/identity/payload redaction; PNG content review is separate',
            'raw_profile_database_directories_read':False},payloads


def fingerprint(source,evidence):
    return sha(json.dumps({'source':source,'evidence':evidence},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())


def archive(path: Path,payloads: dict,manifest_name: str,manifest: dict):
    names=[safe_name(name).casefold() for name in payloads]
    if len(set(names))!=len(names) or safe_name(manifest_name).casefold() in names: raise ValueError('Archive member/manifest collision')
    plain(path); path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for name,data in sorted(payloads.items()):
            info=zipfile.ZipInfo(safe_name(name),(2026,10,30,0,0,0)); info.compress_type=zipfile.ZIP_DEFLATED; info.external_attr=0o100644<<16
            bundle.writestr(info,data)
        bundle.writestr(manifest_name,json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    with zipfile.ZipFile(path) as bundle:
        if bundle.testzip() is not None: raise ValueError('ZIP CRC failed')
        if len(bundle.namelist())!=len(payloads)+1: raise ValueError('ZIP member count mismatch')
        for name,data in payloads.items():
            if bundle.read(name)!=data: raise ValueError('ZIP payload byte mismatch')
    return {'path':str(path),'bytes':path.stat().st_size,'sha256':sha(path.read_bytes()),'file_count':len(payloads)+1}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=ROOT); parser.add_argument('--delivery',type=Path,default=DELIVERY)
    parser.add_argument('--plan-output',type=Path,default=DELIVERY/'packaging/dry-run-plan.json')
    parser.add_argument('--write',action='store_true'); parser.add_argument('--plan',type=Path)
    args=parser.parse_args()
    source,source_data=source_plan(args.source_root.absolute()); evidence,evidence_data=evidence_plan(args.delivery.absolute())
    identity=fingerprint(source,evidence)
    blocked=source['privacy']['unreviewed']+evidence['privacy']['unreviewed']
    result={'schema':1,'kind':'health-release-source-evidence-plan','version':VERSION,'created_utc':datetime.now(timezone.utc).isoformat(),
        'dry_run':not args.write,'status':'REVIEW_REQUIRED' if blocked else 'READY_FOR_FROZEN_REVIEW','fingerprint':identity,
        'source':source,'evidence':evidence,'git_operations':0,'network_operations':0,'ui_or_server_executed':False,
        'acceptance_not_executed_by_packager':['server real-content CJS contract; status must come from root runtime evidence','production timer and real PC'],
        'archive_names':[f'SignalDesk-{VERSION}-source.zip',f'health-channel-{VERSION}-evidence.zip']}
    if args.write:
        if not args.plan or blocked: raise ValueError('Write requires an unblocked, explicitly frozen dry-run plan')
        if source.get('activation_source_freeze_pending',False):
            raise ValueError('Root activation source final freeze is pending')
        frozen=load_json(args.plan)
        if frozen['fingerprint']!=identity: raise ValueError('Source/evidence changed since the frozen plan; re-run dry-run')
        output=args.delivery/'packages'
        # Both must be new; a partial prior output must be reviewed separately.
        for name in result['archive_names']:
            if (output/name).exists(): raise ValueError('Never overwrite an existing delivery archive')
        result['archives']=[archive(output/result['archive_names'][0],source_data,'SOURCE-MANIFEST.json',source),
                            archive(output/result['archive_names'][1],evidence_data,'EVIDENCE-MANIFEST.json',evidence)]
    plain(args.plan_output); args.plan_output.parent.mkdir(parents=True,exist_ok=True)
    args.plan_output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'dry_run':result['dry_run'],'source_files':source['file_count'],'evidence_files':evidence['file_count'],
        'source_unreviewed':len(source['privacy']['unreviewed']),'evidence_unreviewed':len(evidence['privacy']['unreviewed']),
        'png_pending_review':len(evidence['omitted_png']),'fingerprint':identity,'archives_created':len(result.get('archives',[]))},ensure_ascii=False))
    return 2 if blocked else 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (ValueError,OSError,KeyError) as error:
        print(json.dumps({'status':'FAIL','error_type':type(error).__name__,'message':str(error),
            'archives_created':'inspect exclusive outputs after write failure' if '--write' in __import__('sys').argv else 0},ensure_ascii=False))
        raise SystemExit(1)
