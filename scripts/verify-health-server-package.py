"""Verify the public increment; explicit local mode never runs host/systemd."""
import argparse,hashlib,json,subprocess,sys,tarfile,tempfile
from pathlib import Path

EXPECTED={'app/'+name+'.py' for name in ['articles','schemas','channel_aliases','channel_reconcile','health_delivery','health_collector','health_relay']}
EXPECTED|={'deploy/signaldesk-health-'+mode+'.'+kind for mode in ['prepare','deliver'] for kind in ['service','timer']}


def require(value, reason):
 if not value:raise ValueError(reason)


def verify(archive, checksum, local_source_root=None):
 require(not archive.is_symlink() and archive.is_file(),'Regular archive required')
 if local_source_root is None:
  require(archive.parent==Path('/var/tmp') and (archive.name.startswith('signaldesk-health-publish-')
   or archive.name=='signaldesk-health-backend-0.6.4-rss-ack.tar.gz'),'Managed host archive path required')
 require(hashlib.sha256(archive.read_bytes()).hexdigest()==checksum,'Archive SHA mismatch')
 payload={}
 with tarfile.open(archive) as tar:
  for member in tar:
   require(member.isfile() and member.name in EXPECTED and member.name not in payload,'Unexpected archive member')
   require(0 < member.size <= 2*1024*1024,'Bounded public member required')
   data=tar.extractfile(member).read(2*1024*1024+1)
   require(len(data)==member.size,'Archive member size mismatch')
   if member.name.endswith('.py'):compile(data,member.name,'exec')
   payload[member.name]=data
 require(set(payload)==EXPECTED,'Exact eleven public members required')
 info={'passed':True,'archive_sha256':checksum,'members':len(payload),'python_modules_compiled':7,
  'python_runtime':sys.version.split()[0],'files_installed':False,'services_changed':False,'messages_sent':0,
  'member_sha256':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(payload.items())}}
 if local_source_root is not None:
  for name,data in payload.items():
   source=local_source_root/'server'/name
   require(all(not path.is_symlink() and not getattr(path,'is_junction',lambda:False)() for path in (source,*source.parents)),
           'Linked local source rejected')
   require(source.is_file() and source.read_bytes()==data,'Archive/source bytes differ')
  return {**info,'scope':'local_archive_and_current_source','local_source_members_matched':11,
   'actual_systemd_unit_verification':False,'actual_task_python310_compile':False,'network_operations':0}
 with tempfile.TemporaryDirectory(prefix='signaldesk-health-unit-verify-',dir='/var/tmp') as stage:
  paths=[]
  for name,data in payload.items():
   if name.startswith('deploy/'):
    target=Path(stage)/Path(name).name;target.write_bytes(data);paths.append(str(target))
  result=subprocess.run(['systemd-analyze','verify',*paths],capture_output=True,timeout=30)
  info.update(passed=result.returncode==0,scope='actual_host_unit_syntax',actual_systemd_unit_verification=True,
              verify_exit_code=result.returncode)
  if result.returncode:
   info.update(verify_error_sha256=hashlib.sha256(result.stderr).hexdigest(),verify_error_bytes=len(result.stderr))
  return info


def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--sha256',required=True)
 p.add_argument('--local-source-root',type=Path,help='Compare every member to this local source; do not run systemd or network.')
 a=p.parse_args(argv);info=verify(a.archive,a.sha256,a.local_source_root)
 print(json.dumps(info,sort_keys=True));return 0 if info['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
