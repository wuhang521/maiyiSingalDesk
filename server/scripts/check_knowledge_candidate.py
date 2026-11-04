"""Import and migrate a NEW candidate using a private synthetic SQLite database."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--candidate', required=True, type=Path)
args = parser.parse_args()
candidate = args.candidate.resolve()
if candidate.parent != Path('/var/tmp') or not candidate.name.startswith('signaldesk-knowledge-candidate-'):
    raise SystemExit('Expected private knowledge candidate')
database = candidate / ('acceptance-' + uuid.uuid4().hex + '.db')
if database.exists(): raise SystemExit('Refusing to reuse a database')
os.environ['TASK_DATABASE_URL'] = 'sqlite:///' + str(database)
os.environ['TASK_API_KEY'] = 'synthetic-candidate-api-key'
os.chdir(candidate)
sys.path.insert(0, str(candidate))
from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import Staff, UserAccount, Task
from sqlalchemy import select
for table in Base.metadata.sorted_tables:
    if not table.name.startswith('knowledge_'): table.create(engine)
with engine.begin() as connection:
    connection.exec_driver_sql('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)')
    connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('20260928_01')")
with SessionLocal() as db:
    staff = Staff(name='Synthetic legacy staff'); db.add(staff); db.flush()
    user = UserAccount(username='synthetic-legacy', staff_id=staff.id, password_hash='synthetic-not-used', role='member'); db.add(user)
    db.add(Task(title='Synthetic legacy task', content='Preserve existing task', assignee_id=staff.id, creator_name='Synthetic tester'))
    db.commit(); owner = user.id
def upgrade(target):
    result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', target], cwd=candidate, env=os.environ,
                            capture_output=True, text=True, timeout=30)
    if result.returncode: raise RuntimeError('Candidate migration failed; inspect private stage diagnostics')
upgrade('20261008_03')
from app.knowledge_models import KnowledgeItem
with SessionLocal() as db:
    db.add(KnowledgeItem(id=uuid.uuid4().hex, owner_id=owner, title='Synthetic legacy KB', content='Preserve reviewed original', status='ready', kind='note'))
    db.commit()
upgrade('head'); upgrade('head')
with SessionLocal() as db:
    assert db.scalar(select(Staff.name)) == 'Synthetic legacy staff'
    assert db.scalar(select(UserAccount.username)) == 'synthetic-legacy'
    assert db.scalar(select(Task.title)) == 'Synthetic legacy task'
    assert db.scalar(select(KnowledgeItem.content)) == 'Preserve reviewed original'
with sqlite3.connect(database) as db:
    version = db.execute('SELECT version_num FROM alembic_version').fetchone()[0]
    assert version == '20261008_12'
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_%'")]
assert len(tables) == 23
routes = {getattr(route, 'path', '') for route in app.routes}
assert '/api/v1/knowledge/teams' in routes and '/api/v1/knowledge/capture/files' in routes and '/api/v1/knowledge/costs' in routes
print(json.dumps(dict(passed=True, schema=version, knowledge_tables=len(tables),
                     preserved=['staff', 'personal account', 'task', 'reviewed KB original'],
                     repeated_upgrade=True, imports=True, production_changed=False)))
