"""Read-only NSIS archive CRC and three payload hashes; never runs the installer."""
import argparse,hashlib,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--sevenzip',type=Path,required=True);p.add_argument('--installer',type=Path,required=True);p.add_argument('--package-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
assert a.sevenzip.is_file() and a.installer.is_file() and a.package_dir.is_dir()
metadata=subprocess.run(['node','-e',"process.stdout.write(require('@electron/asar').extractFile(process.argv[1], 'package.json'))",str((a.package_dir/'resources/app.asar').resolve())],cwd=Path(__file__).resolve().parents[1],capture_output=True,check=True,timeout=30)
version=json.loads(metadata.stdout)['version']
assert isinstance(version,str) and version,'Missing packaged application version'
a.output.parent.mkdir(parents=True,exist_ok=True)
r=subprocess.run([str(a.sevenzip),'t','-sccUTF-8',str(a.installer)],capture_output=True,timeout=300)
a.output.with_suffix('.crc.log').write_bytes(r.stdout+r.stderr)
assert r.returncode==0 and b'Everything is Ok' in r.stdout,'Installer archive CRC check failed'
r=subprocess.run([str(a.sevenzip),'l','-slt','-sccUTF-8',str(a.installer)],capture_output=True,check=True,timeout=60)
a.output.with_suffix('.listing.log').write_bytes(r.stdout)
paths=[line[7:].replace('\\','/') for line in r.stdout.decode('utf-8').splitlines() if line.startswith('Path = ')]
rows=[]
for relative in ['迈医协同.exe','resources/app.asar','resources/video-runtime/manifest.json']:
 matches=[name for name in paths if name==relative or name.endswith('/'+relative)];assert len(matches)==1,(relative,len(matches))
 process=subprocess.Popen([str(a.sevenzip),'e','-so',str(a.installer),matches[0]],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 digest=hashlib.sha256();size=0
 for block in iter(lambda:process.stdout.read(1024*1024),b''):digest.update(block);size+=len(block)
 diagnostic=process.stderr.read();assert process.wait(timeout=180)==0,'Payload extraction failed'
 source=a.package_dir/relative;expected=hashlib.sha256(source.read_bytes()).hexdigest()
 assert digest.hexdigest()==expected and size==source.stat().st_size,'Installer payload mismatch: '+relative
 rows.append({'path':relative,'archive_member':matches[0],'sha256':expected,'bytes':size})
result={'passed':True,'version':version,'version_source':'packaged resources/app.asar/package.json','installer_sha256':hashlib.sha256(a.installer.read_bytes()).hexdigest(),'installer_bytes':a.installer.stat().st_size,'archive_crc_passed':True,'payload_matches_tested_program':rows,'nsis_executed':False,'warning':'7-Zip may report data after archive end for NSIS; CRC command returned0 and Everything is Ok'}
a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'passed':True,'payload_hashes':len(rows),'nsis_executed':False}))
