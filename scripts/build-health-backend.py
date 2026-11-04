"""Package the exact reviewed health-only production increment; no config/data."""
import gzip, hashlib, io, json, tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
delivery = Path('D:/maiyi/docs/delivery/health-channel-push-20261030')
out = delivery / 'packages'
out.mkdir(exist_ok=True)
names = ['app/' + name + '.py' for name in
         ['articles', 'schemas', 'channel_aliases', 'channel_reconcile', 'health_delivery', 'health_collector', 'health_relay']]
names += ['deploy/signaldesk-health-' + mode + '.' + kind
          for mode in ['prepare', 'deliver'] for kind in ['service', 'timer']]
archive = out / 'signaldesk-health-backend-0.6.4-rss-ack.tar.gz'
manifest = out / 'backend-increment-rss-ack-manifest.json'
if archive.exists() or manifest.exists():
    raise FileExistsError('Final RSS ACK increment must use new output paths; historical bytes are retained')
critical = {
    'app/health_delivery.py': '91e5f649ff6773b08b0ba40e92656ed5457b42c811ac873c2a1d3302f1a3130d',
    'app/health_collector.py': '04e179b61df6d86e3c714c49fca297e9517fc01b8df674c083743d0fa87cf5be',
}
members = []
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as tar:
    for name in names:
        source = root / 'server' / name
        if any(path.is_symlink() or getattr(path, 'is_junction', lambda: False)() for path in (source, *source.parents)):
            raise ValueError('Linked source paths are forbidden')
        data = source.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        if name in critical and checksum != critical[name]:
            raise ValueError('Root-frozen RSS ACK source differs')
        if name.endswith('.py'):
            compile(data, name, 'exec')
        member = tarfile.TarInfo(name)
        member.size = len(data)
        member.mode = 0o644
        member.mtime = 0
        tar.addfile(member, io.BytesIO(data))
        members.append({'path': name, 'bytes': len(data), 'sha256': checksum})
data = gzip.compress(buffer.getvalue(), mtime=0)
with archive.open('xb') as stream:
    stream.write(data)
result = {'archive': str(archive), 'bytes': archive.stat().st_size,
          'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
          'members': members, 'critical_sha256': critical,
          'private_config_or_data_included': False, 'historical_packages_overwritten': False}
with manifest.open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
print(json.dumps(result))
