"""Validate a hashed code-only payload in a NEW private staging directory."""
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
if archive.parent != Path('/var/tmp') or not archive.name.startswith('signaldesk-knowledge-validate-') or archive.is_symlink():
    raise SystemExit('Invalid staging archive')
if hashlib.sha256(archive.read_bytes()).hexdigest() != args.sha256:
    raise SystemExit('Payload hash mismatch')
stage = archive.with_name(archive.name.removesuffix('.tar.gz'))
if stage.exists(): raise SystemExit('Refusing to reuse a staging directory')
os.umask(0o077)
stage.mkdir(mode=0o700)
with tarfile.open(archive) as source:
    for member in source.getmembers():
        relative = Path(member.name)
        target = stage / relative
        if relative.is_absolute() or '..' in relative.parts or not member.isfile() or not target.resolve().is_relative_to(stage.resolve()):
            raise SystemExit('Unsafe payload member')
        if member.name != 'synthetic-speech.wav' and not (member.name.startswith('server/') and (relative.suffix == '.py' or relative.name in {'requirements.txt', 'alembic.ini'})):
            raise SystemExit('Unexpected payload file')
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.extractfile(member) as reader, target.open('xb') as writer:
            writer.write(reader.read())
python = next(p for p in [Path('/opt/signaldesk-task/.venv/bin/python'), Path('/opt/signaldesk-task/venv/bin/python')] if p.is_file())
result = subprocess.run([str(python), str(stage / 'server/scripts/knowledge_qwen_smoke.py'), '--audio', str(stage / 'synthetic-speech.wav')],
                        cwd=stage / 'server', capture_output=True, text=True, timeout=180,
                        env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'})
print(json.dumps(dict(stage=str(stage), code=result.returncode, result=result.stdout.strip(),
                     diagnostic='See safe adapter result' if result.returncode else '',
                     production_changed=False), ensure_ascii=False, indent=2))
raise SystemExit(result.returncode)
