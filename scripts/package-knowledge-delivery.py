"""Package runnable source/extension/plugin with member hashes, never local data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))['version']
SKIP = {'.git', 'node_modules', '__pycache__', '.pytest_cache', '.venv', '.venv-signaldesk', 'artifacts', 'release', 'dist', 'coverage', 'test-results'}
EXTENSIONS = {'.py', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.json', '.html', '.css', '.md', '.txt', '.yml', '.yaml', '.toml', '.ini', '.mako', '.ps1', '.sh', '.service', '.svg', '.png', '.webp', '.ico', '.ttf', '.otf'}
ROOT_NAMES = {'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts', 'index.html', 'README.md', 'PRODUCT.md', '.env.example', '.gitignore', '启动新版体验.ps1'}
PUBLIC_FIXTURES = {'server/tests/fixtures/public-tencent-fixture.amr': 'e267dac1cef824b188e5c8659a513bbe147dc140fbefdf90ec2fc92ea0c734f3'}

def allowed(file):
    relative = file.relative_to(ROOT)
    if any(part in SKIP for part in relative.parts) or file.is_symlink() or getattr(file, 'is_junction', lambda: False)(): return False
    if file.name.startswith('.env') and file.name != '.env.example': return False
    if file.name in {'credentials.json', 'auth-session.json', 'local-login.json', 'video-config.json', 'running.json'}: return False
    fixture_hash = PUBLIC_FIXTURES.get(relative.as_posix())
    if fixture_hash:
        if hashlib.sha256(file.read_bytes()).hexdigest() != fixture_hash:
            raise ValueError('Public fixture hash changed: ' + relative.as_posix())
        return True
    return file.is_file() and (file.suffix.lower() in EXTENSIONS or file.name in ROOT_NAMES or file.name == '.env.example')

def digest(data): return hashlib.sha256(data).hexdigest()

def package(output, name, files):
    rows = []
    files = sorted(set(files))
    for file in files:
        data = file.read_bytes()
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{16}', data) or re.search(rb'\bsk-[A-Za-z0-9]{32,}\b', data):
            raise ValueError('Potential credential in source file: ' + file.relative_to(ROOT).as_posix())
        rows.append(dict(path=file.relative_to(ROOT).as_posix(), bytes=len(data), sha256=digest(data)))
    archive = output / name
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for file in files: bundle.write(file, arcname=file.relative_to(ROOT).as_posix())
        bundle.writestr('SOURCE-MANIFEST.json', json.dumps(dict(version=VERSION, files=rows), ensure_ascii=False, indent=2))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None: raise ValueError('Archive CRC verification failed')
        for row in rows:
            if digest(bundle.read(row['path'])) != row['sha256']: raise ValueError('Source member hash mismatch')
    manifest = dict(archive=name, sha256=digest(archive.read_bytes()), bytes=archive.stat().st_size, files=rows,
                    excluded=['credentials', '.env except examples', 'databases', 'local profiles', 'node_modules', 'media originals', 'caches', 'build outputs'])
    (output / (name + '.manifest.json')).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {key: manifest[key] for key in ('archive', 'sha256', 'bytes')} | {'file_count': len(rows)}

def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    source = [file for folder in ['src', 'electron', 'server', 'scripts', 'browser-extension', 'hermes-plugin', 'hermes-skill', 'config', 'build', '.github'] for file in (ROOT / folder).rglob('*') if allowed(file)]
    source += [file for file in ROOT.iterdir() if file.is_file() and (file.name in ROOT_NAMES or file.name.startswith('KNOWLEDGE-') or file.name == 'WECHAT-KNOWLEDGE-BASE-PLAN.md') and allowed(file)]
    reports = [package(args.output, f'SignalDesk-{VERSION}-source.zip', source)]
    for folder, name in [('browser-extension', f'knowledge-browser-extension-{VERSION}.zip'), ('hermes-plugin/wechat-knowledge', f'knowledge-hermes-plugin-{VERSION}.zip')]:
        reports.append(package(args.output, name, [file for file in (ROOT / folder).rglob('*') if allowed(file)]))
    print(json.dumps(dict(passed=True, artifacts=reports), ensure_ascii=False))

if __name__ == '__main__': main()
