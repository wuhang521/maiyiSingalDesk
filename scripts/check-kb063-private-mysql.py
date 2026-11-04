"""Private socket-only MySQL acceptance. Never logs into production MySQL."""
import argparse,hashlib,json,os,pwd,secrets,shutil,subprocess,tarfile,time
from pathlib import Path
import pymysql
from sqlalchemy.engine import URL
p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--sha256',required=True);a=p.parse_args()
assert a.archive.parent==Path('/var/tmp') and a.archive.name.startswith('signaldesk-kb063-test-') and not a.archive.is_symlink()
assert hashlib.sha256(a.archive.read_bytes()).hexdigest()==a.sha256
os.umask(0o077);stage=Path('/var/tmp')/('signaldesk-kb063-source-'+secrets.token_hex(6));stage.mkdir(mode=0o700)
with tarfile.open(a.archive) as tar:
 for member in tar:
  relative=Path(member.name);target=stage/relative
  assert member.isfile() and not relative.is_absolute() and '..' not in relative.parts and target.resolve().is_relative_to(stage)
  assert (member.name.startswith('server/app/') and relative.suffix=='.py') or member.name in {'scripts/knowledge-ingress-mysql-smoke.py','public-test.amr'}
  target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(tar.extractfile(member).read())
work=Path('/var/tmp')/('signaldesk-kb063-mysql-'+secrets.token_hex(6));work.mkdir(mode=0o700);owner=pwd.getpwnam('mysql');os.chown(work,owner.pw_uid,owner.pw_gid)
socket=work/'mysql.sock';data=work/'data';process=None;connection=None;result={'passed':False,'production_changed':False,'tcp_listening':False};log=(work/'mysqld.private.log').open('w')
try:
 r=subprocess.run(['/usr/sbin/mysqld','--no-defaults','--initialize-insecure','--user=mysql','--datadir='+str(data)],stdout=log,stderr=log,timeout=90)
 assert r.returncode==0,'Private MySQL initialization failed'
 process=subprocess.Popen(['/usr/sbin/mysqld','--no-defaults','--user=mysql','--datadir='+str(data),'--socket='+str(socket),'--pid-file='+str(work/'mysql.pid'),'--log-error='+str(work/'mysql.log'),'--skip-networking','--mysqlx=0','--skip-log-bin','--performance-schema=OFF','--innodb-buffer-pool-size=67108864','--innodb-redo-log-capacity=67108864','--max-connections=20','--table-open-cache=64'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
 for _ in range(120):
  if process.poll() is not None:break
  if socket.is_socket():
   try:connection=pymysql.connect(unix_socket=str(socket),user='root',charset='utf8mb4',connect_timeout=2,autocommit=True);break
   except pymysql.Error:pass
  time.sleep(.5)
 assert connection is not None,'Private MySQL did not start'
 schema='signaldesk_kb_test_ingress_'+secrets.token_hex(5);password=secrets.token_urlsafe(32)
 with connection.cursor() as cursor:
  cursor.execute('SELECT VERSION()');version=cursor.fetchone()[0]
  cursor.execute("SHOW VARIABLES LIKE 'skip_networking'");assert cursor.fetchone()[1]=='ON'
  cursor.execute('CREATE DATABASE `'+schema+'` CHARACTER SET utf8mb4')
  cursor.execute("CREATE USER 'kb_validation'@'localhost' IDENTIFIED BY %s",(password,))
  cursor.execute('GRANT ALL PRIVILEGES ON `'+schema+"`.* TO 'kb_validation'@'localhost'")
 url=URL.create('mysql+pymysql',username='kb_validation',password=password,host='localhost',database=schema,query={'unix_socket':str(socket)})
 env={key:os.environ[key] for key in ['PATH','LANG'] if key in os.environ};env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',KNOWLEDGE_TEST_DATABASE_URL=url.render_as_string(hide_password=False))
 r=subprocess.run(['/opt/signaldesk-task/.venv/bin/python',str(stage/'scripts/knowledge-ingress-mysql-smoke.py'),'--source-root',str(stage),'--sample',str(stage/'public-test.amr')],cwd=stage,env=env,capture_output=True,text=True,timeout=150)
 try:details=json.loads(r.stdout.strip().splitlines()[-1])
 except Exception:details={'passed':False,'detail':'Runner did not return a safe result'}
 result.update(passed=r.returncode==0 and details.get('passed') is True,mysql_version=version,acceptance=details,source_stage=str(stage),gateway_sha256=hashlib.sha256((stage/'server/app/knowledge_gateway.py').read_bytes()).hexdigest())
 with connection.cursor() as cursor:
  cursor.execute('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s',(schema,));assert cursor.fetchone()[0]==0,'Test schema still has tables'
  cursor.execute('DROP DATABASE `'+schema+'`');cursor.execute("DROP USER 'kb_validation'@'localhost'")
 result['test_schema_and_account_removed']=True
except Exception as error:
 result.update(error_type=type(error).__name__,detail=str(error)[:150] if isinstance(error,AssertionError) else 'Private test did not finish; credentials retained only in process memory')
finally:
 if connection:
  try:
   with connection.cursor() as cursor:cursor.execute('SHUTDOWN')
  except pymysql.Error:pass
  connection.close()
 if process:
  try:process.wait(timeout=20)
  except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=15)
 result['private_mysql_stopped']=not process or process.poll() is not None
 log.close()
 # Only this newly generated, verified tree contains disposable test data.
 if result.get('passed') and result['private_mysql_stopped']:
  assert work.resolve().parent==Path('/var/tmp') and work.name.startswith('signaldesk-kb063-mysql-')
  shutil.rmtree(work);result['temporary_mysql_files_cleaned']=True
print(json.dumps(result))
raise SystemExit(0 if result.get('passed') and result.get('private_mysql_stopped') else 1)
