"""Build synthetic MySQL test source. Never includes credentials or user data."""
import hashlib,io,json,tarfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
delivery=Path('D:/maiyi/docs/delivery/health-channel-push-20261030')
archive=delivery/'health-mysql-relay-test-source.tar.gz'
paths=sorted((root/'server/app').rglob('*.py'))+[root/'server/scripts/test_health_channels_mysql.py']
members=[]
with tarfile.open(archive,'w:gz') as tar:
 for path in paths:
  assert path.is_file() and not path.is_symlink()
  name=path.relative_to(root).as_posix();data=path.read_bytes();compile(data,name,'exec')
  info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600;info.mtime=0
  tar.addfile(info,io.BytesIO(data));members.append({'path':name,'sha256':hashlib.sha256(data).hexdigest()})
result={'archive':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size,'members':members,'configuration_or_data_included':False}
(delivery/'health-mysql-relay-source-manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='members'}))
