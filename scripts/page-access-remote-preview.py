"""Preview public permission patch on the authorized host without exporting source."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

EVIDENCE = Path('D:/maiyi/docs/delivery/desktop-account-permissions-20261110/evidence/page-backend-20261111')

def main():
    manifest = json.loads((EVIDENCE / 'manifest.json').read_text('utf-8'))
    archive = (EVIDENCE / 'page-task-backend-preview.tar.gz').read_bytes()
    assert hashlib.sha256(archive).hexdigest() == manifest['archive']['sha256']
    remote = '''import ast,base64,hashlib,io,json,sys,tarfile
from pathlib import Path
sys.dont_write_bytecode=True
root=Path('/opt/signaldesk-task')
manifest=json.loads(MANIFEST)
blob=base64.b64decode(ARCHIVE)
assert hashlib.sha256(blob).hexdigest()==manifest['archive']['sha256']
with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as tar:
    members=tar.getmembers()
    assert len(members)==10 and all(m.isfile() for m in members)
    assert set(m.name for m in members)==set(manifest['archive']['members'])
    contents={m.name:tar.extractfile(m).read() for m in members}
assert all(hashlib.sha256(data).hexdigest()==manifest['archive']['members'][name] for name,data in contents.items())
for row in manifest['files']:
    target=root/row['target']
    assert not target.is_symlink()
    if row['before']['exists']:
        assert hashlib.sha256(target.read_bytes()).hexdigest()==row['before']['sha256']
    else:
        assert not target.exists()
namespace={'__name__':'preview_only'}
exec(compile(contents['main_patch.py'],'public-main-patch.py','exec'),namespace)
after,metadata=namespace['patch_main']((root/'app/main.py').read_bytes(),json.loads(contents['main-recipe.json']))
result={'passed':True,'mode':'read_only_in_memory_preview','main':metadata,'verified_public_members':len(contents),'production_code_writes':0,'database_writes':0,'private_source_exported':False}
print(json.dumps(result,sort_keys=True))
'''.replace('MANIFEST', repr(json.dumps(manifest))).replace('ARCHIVE', repr(base64.b64encode(archive).decode()))
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', 'hermes-desktop-vps',
        'cd /opt/signaldesk-task && .venv/bin/python -'], input=remote, text=True, capture_output=True, timeout=60)
    if result.returncode:
        print(json.dumps({'passed': False, 'remote_exit_code': result.returncode, 'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr)}))
        raise SystemExit(result.returncode)
    record = json.loads(result.stdout)
    assert record['passed'] and record['main']['unrelated_ast_preserved']
    (EVIDENCE / 'production-main-preview.json').write_text(json.dumps(record, indent=2) + '\n', 'utf-8')
    print(json.dumps(record))

if __name__ == '__main__':
    main()
