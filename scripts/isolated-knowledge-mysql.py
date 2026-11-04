"""Private same-version MySQL validation instance. No production admin login."""
import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import time
import pymysql
from sqlalchemy.engine import URL

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--candidate', type=Path, required=True)
p.add_argument('--test-script', type=Path, required=True)
args = p.parse_args()
candidate = args.candidate.resolve()
if candidate.parent != Path('/var/tmp') or not candidate.name.startswith('signaldesk-knowledge-candidate-'):
    raise SystemExit('Invalid candidate')
work = Path('/var/tmp') / ('signaldesk-kb-mysql-' + secrets.token_hex(5))
work.mkdir(mode=0o700)
account = pwd.getpwnam('mysql'); os.chown(work, account.pw_uid, account.pw_gid)
data = work / 'data'; socket = work / 'mysql.sock'
log = (work / 'commands.private.log').open('w')
init = subprocess.run(['/usr/sbin/mysqld', '--no-defaults', '--initialize-insecure', '--user=mysql', '--datadir=' + str(data)],
                      stdout=log, stderr=log, timeout=90)
if init.returncode: raise SystemExit('Private MySQL initialization failed; no production changes')
process = subprocess.Popen(['/usr/sbin/mysqld', '--no-defaults', '--user=mysql', '--datadir=' + str(data),
    '--socket=' + str(socket), '--pid-file=' + str(work / 'mysql.pid'), '--log-error=' + str(work / 'mysql.log'),
    '--skip-networking', '--mysqlx=0', '--skip-log-bin', '--performance-schema=OFF',
    '--innodb-buffer-pool-size=67108864', '--innodb-redo-log-capacity=67108864', '--max-connections=15', '--table-open-cache=64'],
    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
ready = False
for _ in range(120):
    if process.poll() is not None: break
    if socket.is_socket():
        try:
            connection = pymysql.connect(unix_socket=str(socket), user='root', charset='utf8mb4', connect_timeout=2, autocommit=True)
            ready = True; break
        except pymysql.Error: pass
    time.sleep(0.5)
if not ready:
    process.terminate(); raise SystemExit('Private MySQL startup failed')
user = 'kb_validation'; password = secrets.token_urlsafe(36)
with connection.cursor() as cursor:
    cursor.execute("CREATE USER %s@'localhost' IDENTIFIED BY %s", (user, password))
    cursor.execute(r"GRANT ALL PRIVILEGES ON `signaldesk\_kb\_test\_%`.* TO 'kb_validation'@'localhost'")
    cursor.execute('SELECT VERSION()'); version = cursor.fetchone()[0]
connection.close()
env_file = work / 'test.env'
url = URL.create('mysql+pymysql', username=user, password=password, host='localhost', database='unused', query={'unix_socket': str(socket)})
env_file.write_text('KNOWLEDGE_TEST_DATABASE_URL=' + url.render_as_string(hide_password=False) + '\n'); env_file.chmod(0o600)
state = {'work': str(work), 'socket': str(socket), 'pid': process.pid, 'test_env': str(env_file), 'candidate': str(candidate), 'version': version}
(work / 'state.json').write_text(json.dumps(state)); (work / 'state.json').chmod(0o600)
wrapper = '''import json,runpy,sys,traceback
script=sys.argv.pop(1)
module=runpy.run_path(script,run_name="knowledge_validation_module")
try: raise SystemExit(module["main"]())
except Exception as error:
 original=getattr(error,"orig",error)
 code=original.args[0] if original.args and isinstance(original.args[0],int) else None
 print(json.dumps({"passed":False,"error_type":type(error).__name__,"sql_code":code,"frames":[{"file":f.filename.split("/")[-1],"line":f.lineno,"function":f.name} for f in traceback.extract_tb(error.__traceback__)[-6:]]}))
 raise SystemExit(1)
'''
result = subprocess.run(['/opt/signaldesk-task/.venv/bin/python', '-c', wrapper, str(args.test_script), '--candidate', str(candidate), '--test-env', str(env_file)],
                        capture_output=True, text=True, timeout=90, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
report = {'instance': state, 'production_database_changed': False, 'tcp_listening': False, 'concurrency': result.stdout.strip(), 'code': result.returncode}
print(json.dumps(report), flush=True)
if result.returncode:
    connection = pymysql.connect(unix_socket=str(socket), user='root', autocommit=True)
    with connection.cursor() as cursor: cursor.execute('SHUTDOWN')
    connection.close(); process.wait(timeout=15)
raise SystemExit(result.returncode)
