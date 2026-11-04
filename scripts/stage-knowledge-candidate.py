"""Hash-check a source archive and validate a NEW private candidate. Never publish."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--archive', required=True, type=Path)
parser.add_argument('--sha256', required=True)
args = parser.parse_args()
archive = args.archive
if archive.parent != Path('/var/tmp') or not archive.name.startswith('signaldesk-knowledge-final-') or archive.is_symlink():
    raise SystemExit('Invalid private archive')
if hashlib.sha256(archive.read_bytes()).hexdigest() != args.sha256:
    raise SystemExit('Archive hash mismatch')
stage = archive.with_name(archive.name.removesuffix('.tar.gz'))
candidate = stage.with_name(stage.name.replace('signaldesk-knowledge-final-', 'signaldesk-knowledge-candidate-'))
if stage.exists() or candidate.exists(): raise SystemExit('Refusing to reuse a staging directory')
os.umask(0o077)
stage.mkdir(mode=0o700)
allowed = {'requirements.txt', 'alembic.ini', '.env.example', 'script.py.mako'}
with tarfile.open(archive) as source:
    for member in source.getmembers():
        relative = Path(member.name)
        target = stage / relative
        if not member.name.startswith('server/') or relative.is_absolute() or '..' in relative.parts or not member.isfile() or not target.resolve().is_relative_to(stage.resolve()):
            raise SystemExit('Unsafe source member')
        if relative.suffix not in {'.py', '.service'} and relative.name not in allowed:
            raise SystemExit('Unexpected source file')
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.extractfile(member) as reader, target.open('xb') as writer: writer.write(reader.read())
python = '/opt/signaldesk-task/.venv/bin/python'
result = subprocess.run([python, str(stage / 'server/scripts/prepare_knowledge_increment.py'), '--payload', str(stage), '--output', str(candidate)],
                        capture_output=True, text=True, timeout=40)
if result.returncode:
    print(json.dumps(dict(passed=False, step='candidate preparation', production_changed=False)))
    raise SystemExit(result.returncode)
result = subprocess.run([python, str(stage / 'server/scripts/check_knowledge_candidate.py'), '--candidate', str(candidate)],
                        capture_output=True, text=True, timeout=80, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
if result.returncode:
    print(json.dumps(dict(passed=False, step='synthetic acceptance', production_changed=False)))
    raise SystemExit(result.returncode)
manifest = json.loads((candidate / 'knowledge-release-manifest.json').read_text())
for entry in manifest['files']:
    entry['candidate_sha256'] = hashlib.sha256((candidate / entry['path']).read_bytes()).hexdigest()
manifest.update(candidate=str(candidate), archive_sha256=args.sha256, production_changed=False,
                acceptance=json.loads(result.stdout.strip().splitlines()[-1]))
(candidate / 'knowledge-release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest))
