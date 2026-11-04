"""Disposable loopback API/worker for offline code/config rollback acceptance."""
import argparse,os,sys,threading,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--port',type=int,required=True);a=p.parse_args()
expected=Path('D:/maiyi/tmp/kb-install-063-backend').resolve()
assert a.root.resolve().is_relative_to(expected) and a.root.name=='active' and a.root.parent.name.startswith('backend-run-') and a.root.parent.parent.resolve()==expected
os.chdir(a.root);sys.path.insert(0,str(a.root))
from app.main import app
from app.database import Base,engine,SessionLocal
from app.models import Staff,UserAccount
from app.auth import hash_password
from app.config import get_settings
from app.knowledge_worker import run_once
from sqlalchemy import select,text
Base.metadata.create_all(engine)
with engine.begin() as connection:
 connection.execute(text('CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
 if not connection.execute(text('SELECT version_num FROM alembic_version')).first():connection.execute(text("INSERT INTO alembic_version VALUES ('20261008_12')"))
with SessionLocal() as db:
 if not db.scalar(select(UserAccount.id).where(UserAccount.username=='rollback-test')):
  for name in ['rollback-test','rollback-other']:
   db.add(UserAccount(username=name,staff=Staff(name='Synthetic '+name),role='member',password_hash=hash_password('synthetic-only-password')))
  db.commit()
@app.get('/acceptance-info')
def info():
 with engine.connect() as connection:schema=connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
 return {'silk_enabled':get_settings().knowledge_silk_enabled,'schema':schema,'synthetic_only':True}
stop=threading.Event()
def work():
 while not stop.is_set():
  try:
   if not run_once():stop.wait(.15)
  except Exception:stop.wait(.15)
thread=threading.Thread(target=work,daemon=True);thread.start()
import uvicorn
try:uvicorn.run(app,host='127.0.0.1',port=a.port,log_level='warning')
finally:stop.set();thread.join(2)
