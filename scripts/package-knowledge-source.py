"""Build a code-only backend validation payload, excluding env, databases and keys."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--audio', type=Path)
args = parser.parse_args()
files = []
for folder in ['server/app', 'server/migrations', 'server/scripts', 'server/tests']:
    files.extend(p for p in (root / folder).rglob('*.py') if not p.is_symlink() and '__pycache__' not in p.parts)
files.extend(root / name for name in ['server/requirements.txt', 'server/alembic.ini'])
files.extend(root / name for name in ['server/.env.example', 'server/migrations/script.py.mako'])
files.extend((root / 'server/deploy').glob('signaldesk-knowledge-*.service'))
args.output.parent.mkdir(parents=True, exist_ok=True)
manifest = []
with tarfile.open(args.output, 'w:gz') as archive:
    for source in sorted(files):
        relative = source.relative_to(root).as_posix()
        archive.add(source, arcname=relative, recursive=False)
        manifest.append(dict(path=relative, sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    if args.audio:
        archive.add(args.audio, arcname='synthetic-speech.wav', recursive=False)
digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
args.output.with_suffix('.manifest.json').write_text(json.dumps(dict(sha256=digest, files=manifest), indent=2), encoding='utf-8')
print(json.dumps(dict(path=str(args.output), sha256=digest, file_count=len(files), excludes=['.env', 'databases', 'keys', 'user originals'])))
