"""Remove only explicitly listed private MySQL instances created for this release."""
import argparse,json,re,shutil,time
from pathlib import Path
import pymysql
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',action='append',type=Path,required=True);a=p.parse_args();removed=[]
for folder in a.work:
    assert re.fullmatch(r'/var/tmp/signaldesk-kb-mysql-[a-f0-9]{10}',str(folder)) and not folder.is_symlink()
    if not folder.exists():continue
    assert folder.resolve()==folder and (folder/'commands.private.log').exists() and (folder/'data/auto.cnf').exists()
    pidfile=folder/'mysql.pid';socket=folder/'mysql.sock';pid=int(pidfile.read_text()) if pidfile.exists() else None
    proc=Path('/proc')/str(pid) if pid else None
    if proc and proc.exists():
        cmd=(proc/'cmdline').read_bytes().split(b'\0');assert ('--datadir='+str(folder/'data')).encode() in cmd
        assert socket.is_socket()
        conn=pymysql.connect(unix_socket=str(socket),user='root',autocommit=True)
        with conn.cursor() as c:c.execute('SHUTDOWN')
        conn.close()
        for _ in range(40):
            if not socket.exists():break
            time.sleep(.25)
        assert not socket.exists(),'Private instance did not stop'
    assert not socket.exists(),'Refusing to remove a running instance'
    shutil.rmtree(folder);removed.append(str(folder))
print(json.dumps({'passed':True,'removed_private_instances':removed,'production_mysql_changed':False}))
