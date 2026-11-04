"""Build the frozen public two-file RSS update; never include config or data."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path('D:/maiyi/docs/delivery/health-channel-push-20261030/packages')
EXPECTED = {
    'app/health_delivery.py': 'aad239c7566837705a9ef8d9c9bccae90adfa0c3f27eb90fa315816a84bf586e',
    'app/health_collector.py': '04e179b61df6d86e3c714c49fca297e9517fc01b8df674c083743d0fa87cf5be',
}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def main():
    buffer = io.BytesIO()
    rows = []
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as package:
        for name, expected in EXPECTED.items():
            source = ROOT / 'server' / name
            if source.is_symlink() or not source.is_file():
                raise ValueError('Regular public source required')
            data = source.read_bytes()
            if sha(data) != expected:
                raise ValueError('Frozen public source changed')
            compile(data, name, 'exec')
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
            package.addfile(entry, io.BytesIO(data))
            rows.append({'path': name, 'bytes': len(data), 'sha256': expected})
    data = gzip.compress(buffer.getvalue(), mtime=0)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT / ('signaldesk-health-collector-' + sha(data)[:12] + '.tar.gz')
    with destination.open('xb') as stream:
        stream.write(data)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as package:
        if package.getnames() != list(EXPECTED):
            raise ValueError('Archive member mismatch')
        for name, expected in EXPECTED.items():
            if sha(package.extractfile(name).read()) != expected:
                raise ValueError('Archive roundtrip mismatch')
    result = {'archive': str(destination), 'bytes': len(data), 'sha256': sha(data),
              'members': rows, 'before_worker_sha256': '9c7a8995c26456aef4cab1d24b9e5f9bed490a1735279a4a9fffc0f8bb20138d',
              'collector_must_be_absent': True, 'config_or_private_data_included': False}
    manifest = OUTPUT / 'health-collector-update-manifest.json'
    with manifest.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
